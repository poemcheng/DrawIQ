import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import dataclasses,json,sqlite3,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication,QDialog
from workbench.auth import (AuthService,Session,AuthenticationError,SessionExpired,verify_password,
    validate_password,hash_password,normalize_username)
from workbench.auth_ui import LoginDialog,PersonalAccountDialog,PasswordDialog,AccountDialog,UserEditor
from workbench.store import Store
from workbench.ui import MainWindow
from workbench.backup import backup_workspace
from tests.test_workbench import Fixture

INITIAL='Synthetic temporary password 2026!'
CHANGED='A different synthetic password 2026!'
OTHER='Another synthetic password for user!'

class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.time=[1000.]
        self.auth=AuthService(self.root,clock=lambda:self.time[0]); self.auth.bootstrap(INITIAL,must_change=False)
        self.admin=self.auth.authenticate('admin',INITIAL)
    def tearDown(self):self.temp.cleanup()
    def user(self):
        self.auth.create_user(self.admin,'engineer','Test Engineer',OTHER)
        session=self.auth.authenticate('engineer',OTHER)
        return self.auth.change_password(session,OTHER,CHANGED)
    def test_password_hash_is_salted_scrypt_and_not_plaintext(self):
        a=hash_password(INITIAL); b=hash_password(INITIAL)
        self.assertNotEqual(a,b); self.assertTrue(a.startswith('scrypt$17$8$1$'))
        self.assertTrue(verify_password(INITIAL,a)); self.assertFalse(verify_password(CHANGED,a))
        self.assertFalse(verify_password(INITIAL,'malformed'))
        with self.auth.connect() as c:
            contents=str([tuple(r) for r in c.execute('SELECT * FROM users')])+str([tuple(r) for r in c.execute('SELECT * FROM auth_events')])
        self.assertNotIn(INITIAL,contents); self.assertNotIn(self.admin.token,repr(self.admin))
    def test_password_length_common_and_unicode_policy(self):
        for p in ('short','a'*20,'123456789012345'):
            with self.assertRaises(ValueError):validate_password(p)
        self.assertEqual(validate_password('工程確認使用的長密碼 2026 測試'), '工程確認使用的長密碼 2026 測試')
        self.assertEqual(validate_password('Keep spaces at both ends '),'Keep spaces at both ends ')
        with self.assertRaises(ValueError):validate_password('p'*129)
    def test_duplicate_bootstrap_cannot_reset_admin(self):
        with self.assertRaises(ValueError):self.auth.bootstrap(CHANGED)
        self.assertTrue(self.auth.authenticate('admin',INITIAL))
    def test_username_canonicalization_and_duplicate(self):
        self.assertEqual(normalize_username(' ＡＤＭＩＮ '),'admin')
        with self.assertRaises(ValueError):self.auth.create_user(self.admin,'ADMIN','Duplicate',OTHER)
        with self.assertRaises(ValueError):self.auth.create_user(self.admin,'bad/name','Bad',OTHER)
    def test_failed_logins_persist_and_expire_after_five_minutes(self):
        for _ in range(5):
            with self.assertRaises(AuthenticationError):self.auth.authenticate('admin','incorrect')
        reopened=AuthService(self.root,clock=lambda:self.time[0])
        with self.assertRaises(AuthenticationError):reopened.authenticate('admin',INITIAL)
        self.time[0]+=301; self.assertEqual(reopened.authenticate('admin',INITIAL).username,'admin')
        with reopened.connect() as c:
            row=c.execute('SELECT failures,locked_until FROM users WHERE username="admin"').fetchone()
            self.assertEqual(tuple(row),(0,0))
    def test_unknown_and_wrong_login_messages_are_identical(self):
        errors=[]
        for name in ('admin','missing'):
            try:self.auth.authenticate(name,'wrong')
            except AuthenticationError as e:errors.append(str(e))
        self.assertEqual(errors[0],errors[1])
    def test_first_login_is_limited_until_password_changed(self):
        self.auth.create_user(self.admin,'newuser','New User',OTHER)
        session=self.auth.authenticate('newuser',OTHER)
        self.assertTrue(session.must_change_password)
        with self.assertRaises(AuthenticationError):self.auth.require(session)
        with self.assertRaises(AuthenticationError):self.auth.actor(session)
        changed=self.auth.change_password(session,OTHER,CHANGED)
        self.assertFalse(changed.must_change_password); self.auth.require(changed)
        with self.assertRaises(AuthenticationError):self.auth.require(session)
    def test_password_change_requires_current_and_rejects_same(self):
        with self.assertRaises(AuthenticationError):self.auth.change_password(self.admin,'incorrect',CHANGED)
        with self.assertRaises(ValueError):self.auth.change_password(self.admin,INITIAL,INITIAL)
    def test_normal_user_and_forged_role_cannot_manage_accounts(self):
        user=self.user(); forged=dataclasses.replace(user,role='admin',username='admin')
        for session in (user,forged):
            with self.assertRaises(AuthenticationError):self.auth.create_user(session,'attacker','Bad',OTHER)
            with self.assertRaises(AuthenticationError):self.auth.list_users(session)
            with self.assertRaises(AuthenticationError):self.auth.reset_password(session,'admin',OTHER)
            with self.assertRaises(AuthenticationError):self.auth.audit_events(session)
    def test_shared_sign_in_only_enrolls_new_personal_accounts(self):
        enrollment=self.auth.authenticate('user','user')
        self.assertTrue(enrollment.registration_only)
        for admin in (False,True):
            with self.assertRaises(AuthenticationError):self.auth.require(enrollment,admin=admin)
        with self.assertRaises(ValueError):self.auth.register_personal_account(enrollment,'admin','0042')
        with self.assertRaises(ValueError):self.auth.register_personal_account(enrollment,'0042','abc4')
        personal=self.auth.register_personal_account(enrollment,'0042','0042')
        self.assertFalse(personal.must_change_password)
        self.assertEqual(self.auth.require(personal)['role'],'user')
        with self.assertRaises(AuthenticationError):self.auth.register_personal_account(enrollment,'0000','0000')
        newer=self.auth.authenticate('user','user')
        with self.assertRaisesRegex(ValueError,'已存在'):self.auth.register_personal_account(newer,'0042','0000')
        changed=self.auth.change_password(self.auth.authenticate('0042','0042'),'0042','0001')
        self.assertEqual(self.auth.authenticate('0042','0001').username,changed.username)
        with self.assertRaises(ValueError):self.auth.change_password(self.admin,INITIAL,'0001')
    def test_invalid_session_cannot_manage_accounts(self):
        fake=Session('invented','admin','Administrator','admin',False)
        with self.assertRaises(SessionExpired):self.auth.list_users(fake)
    def test_account_listing_excludes_password_hashes(self):
        rows=self.auth.list_users(self.admin); self.assertEqual(len(rows),1)
        self.assertNotIn('password_hash',rows[0]); self.assertNotIn(INITIAL,str(rows))
    def test_disabled_user_cannot_login_and_previous_session_invalidated(self):
        user=self.user(); self.auth.set_enabled(self.admin,'engineer',False)
        with self.assertRaises(AuthenticationError):self.auth.authenticate('engineer',CHANGED)
        with self.assertRaises(SessionExpired):self.auth.require(user)
        self.auth.set_enabled(self.admin,'engineer',True)
        self.assertTrue(self.auth.authenticate('engineer',CHANGED))
    def test_admin_cannot_disable_self(self):
        with self.assertRaises(ValueError):self.auth.set_enabled(self.admin,'admin',False)
    def test_second_admin_can_disable_other_without_removing_last(self):
        self.auth.create_user(self.admin,'otheradmin','Second Administrator',OTHER,'admin')
        other=self.auth.change_password(self.auth.authenticate('otheradmin',OTHER),OTHER,CHANGED)
        self.auth.set_enabled(other,'admin',False)
        with self.assertRaises(AuthenticationError):self.auth.list_users(self.admin)
        with self.assertRaises(ValueError):self.auth.set_enabled(other,'otheradmin',False)
    def test_password_reset_forces_change_and_invalidates_old_sessions(self):
        user=self.user(); self.auth.reset_password(self.admin,'engineer',OTHER)
        with self.assertRaises(SessionExpired):self.auth.require(user)
        with self.assertRaises(AuthenticationError):self.auth.authenticate('engineer',CHANGED)
        self.assertTrue(self.auth.authenticate('engineer',OTHER).must_change_password)
    def test_state_changes_by_another_process_invalidate_sessions(self):
        user=self.user(); other=AuthService(self.root)
        admin2=other.authenticate('admin',INITIAL); other.reset_password(admin2,'engineer',OTHER)
        with self.assertRaises(SessionExpired):self.auth.require(user)
    def test_logout_invalidates_session(self):
        self.auth.logout(self.admin)
        with self.assertRaises(SessionExpired):self.auth.require(self.admin)
    def test_events_are_append_only_and_secret_free(self):
        self.user(); events=self.auth.audit_events(self.admin)
        self.assertTrue(any(e['action']=='password_changed' for e in events)); self.assertNotIn(OTHER,json.dumps(events))
        with self.auth.connect() as c:
            with self.assertRaises(sqlite3.IntegrityError):c.execute('DELETE FROM auth_events')
    def test_full_backup_preserves_accounts_without_plaintext_or_sessions(self):
        store=Store(self.root); backup=backup_workspace(store,self.root/'accounts.zip')
        with zipfile.ZipFile(backup) as z:
            self.assertIn('auth.sqlite3',z.namelist()); raw=z.read('auth.sqlite3'); self.assertNotIn(INITIAL.encode(),raw); self.assertNotIn(self.admin.token.encode(),raw)
            restored=self.root/'restored'; restored.mkdir(); (restored/'auth.sqlite3').write_bytes(raw)
        auth=AuthService(restored); self.assertEqual(auth.authenticate('admin',INITIAL).username,'admin')
    def test_cli_refuses_noninteractive_without_password_arguments(self):
        from workbench.auth import authenticate_cli
        with patch('sys.stdin.isatty',return_value=False):
            with self.assertRaisesRegex(AuthenticationError,'互動登入'):authenticate_cli(self.root,'admin')

class AuthenticationGuiTests(Fixture):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        super().setUp(); self.auth=AuthService(self.store.root); self.auth.bootstrap(INITIAL,must_change=False)
        self.session=self.auth.authenticate('admin',INITIAL)
    def test_login_has_no_document_access_before_authentication(self):
        with patch.object(Store,'list_documents',side_effect=AssertionError('login cannot load documents')):
            dialog=LoginDialog(self.auth); dialog.username.setText('admin'); dialog.password.setText(INITIAL); dialog.submit()
            self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted); self.assertIsNotNone(dialog.session)
            self.assertEqual(dialog.password.text(),'')
    def test_failed_login_keeps_dialog_and_clears_password(self):
        dialog=LoginDialog(self.auth); dialog.username.setText('admin'); dialog.password.setText('wrong'); dialog.submit()
        self.assertIsNone(dialog.session); self.assertEqual(dialog.password.text(),''); self.assertIn('登入未成功',dialog.error.text())
    def test_cancel_forced_password_change_never_opens_session(self):
        self.auth.create_user(self.session,'firstuser','First User',OTHER)
        dialog=LoginDialog(self.auth); dialog.username.setText('firstuser'); dialog.password.setText(OTHER)
        with patch('workbench.auth_ui.PasswordDialog') as change:
            change.return_value.exec.return_value=0; dialog.submit()
        self.assertIsNone(dialog.session); self.assertEqual(dialog.result(),QDialog.DialogCode.Rejected)
    def test_password_dialog_changes_password_and_updates_session(self):
        d=PasswordDialog(self.auth,self.session); d.current.setText(INITIAL); d.new.setText(CHANGED); d.confirm.setText(CHANGED); d.submit()
        self.assertEqual(d.result(),QDialog.DialogCode.Accepted); self.assertNotEqual(d.session.token,self.session.token)
        self.auth.require(d.session); self.assertEqual(d.current.text(),'')
    def test_authenticated_window_starts_empty_and_tracks_actor(self):
        w=MainWindow(self.store.root,auth=self.auth,auth_session=self.session)
        self.assertIsNone(w.doc); self.assertEqual(w.documents.count(),0); self.assertEqual(w.store.actor(),'admin (系統管理者)')
        w.store.edit_requirement(self.doc['id'],self.doc['version'],self.req['id'],{'value':'10'},'auth audit test')
        self.assertEqual(self.store.history(self.doc['id'])[0]['actor'],'admin (系統管理者)'); w.close()
    def test_logout_requires_saved_review_and_business_input(self):
        w=MainWindow(self.store.root,auth=self.auth,auth_session=self.session); w.load_documents()
        w.value.setPlainText('not saved')
        with self.assertRaises(ValueError):w.logout()
        self.auth.require(self.session); w.reset_form(); w.business_page.rfq_fields['customer'].setText('not saved')
        with self.assertRaises(ValueError):w.logout()
        w.business_page.reset(); emitted=[]; w.logout_requested.connect(lambda:emitted.append(True))
        self.auth.logout(self.session)
        w.guard(w.logout)  # Expired sessions must also be able to leave the workbench.
        self.assertEqual(emitted,[True])
        with self.assertRaises(SessionExpired):self.auth.require(self.session)
    def test_regular_user_has_no_account_management_or_backup_access(self):
        self.auth.create_user(self.session,'employee','Employee',OTHER)
        user=self.auth.change_password(self.auth.authenticate('employee',OTHER),OTHER,CHANGED)
        w=MainWindow(self.store.root,auth=self.auth,auth_session=user); dialog=AccountDialog(w)
        self.assertIsNone(dialog.users)
        with self.assertRaises(AuthenticationError):w.backup()
        dialog.close(); w.close()
    def test_admin_add_user_dialog_creates_first_change_account(self):
        editor=UserEditor(self.auth,self.session); editor.username.setText('newstaff'); editor.name.setText('New Staff'); editor.password.setText(OTHER); editor.submit()
        self.assertEqual(editor.result(),QDialog.DialogCode.Accepted)
        self.assertTrue(self.auth.authenticate('newstaff',OTHER).must_change_password)
    def test_fresh_login_enrolls_personal_account_and_keeps_admin_setup_available(self):
        auth=AuthService(self.root/'newworkspace'); d=LoginDialog(auth)
        self.assertFalse(d.setup); self.assertFalse(d.username.isReadOnly())
        def fill_enrollment(registration):
            registration.username.setText('0042'); registration.password.setText('0042'); registration.submit()
            return registration.result()
        d.username.setText('user'); d.password.setText('user')
        with patch.object(PersonalAccountDialog,'exec',fill_enrollment):
            d.submit()
        self.assertEqual(d.result(),QDialog.DialogCode.Accepted)
        self.assertEqual(d.session.username,'0042')
        with self.assertRaises(AuthenticationError):auth.require(d.session,admin=True)
        d=LoginDialog(auth); d.admin_setup_button.click()
        self.assertTrue(d.setup); self.assertTrue(d.username.isReadOnly()); self.assertEqual(d.username.text(),'admin')
        d.password.setText(INITIAL); d.confirm.setText(INITIAL); d.submit(); auth.require(d.session,admin=True)
        self.assertEqual(auth.authenticate('0042','0042').username,'0042')

    def test_cancel_shared_registration_keeps_workspace_locked(self):
        d=LoginDialog(self.auth); d.username.setText('user'); d.password.setText('user')
        with patch.object(PersonalAccountDialog,'exec',return_value=QDialog.DialogCode.Rejected):
            d.submit()
        self.assertIsNone(d.session)
        self.assertEqual(d.password.text(),'')
        self.assertEqual(self.auth.list_users(self.session)[0]['username'],'admin')

if __name__=='__main__':unittest.main()
