"""Native sign-in, first-password change and administrator account management."""
from datetime import datetime
from PyQt6.QtCore import QRegularExpression,QTimer
from PyQt6.QtGui import QRegularExpressionValidator
from PyQt6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,
    QCheckBox,QFormLayout,QComboBox,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QDialogButtonBox,QMessageBox)
from .auth import AuthService,ROLES,SHARED_ACCOUNT,is_pin_account,temporary_password
from . import __version__
from .startup import stage,failure

STYLE='''QDialog{background:#f3f7f8;color:#183c46;font-family:"Microsoft JhengHei UI";font-size:14px;}
QLabel{color:#294a54;background:transparent;}
QLabel#loginTitle{font-size:28px;font-weight:700;color:#123d48;} QLabel#subtle{color:#617b84;font-size:12px;}
QLabel#error{color:#a64032;background:#fff0ec;border-radius:6px;padding:9px;}
QLineEdit,QComboBox{background:white;color:#183c46;border:1px solid #bfcfd4;border-radius:6px;padding:9px;min-height:23px;}
QLineEdit:focus,QComboBox:focus{border:2px solid #16838c;padding:8px;} QLineEdit:read-only{background:#eef3f5;}
QPushButton{background:#fff;border:1px solid #bed2d7;border-radius:6px;padding:9px 16px;color:#204d59;min-height:22px;}
QPushButton:hover{background:#e8f3f4;} QPushButton#primary{background:#0e808b;color:white;border:0;font-weight:600;}
QPushButton#primary:hover{background:#086c78;} QPushButton:disabled{color:#899ea4;background:#e1e9ec;}
QCheckBox{spacing:7px;color:#55727b;} QTableWidget{background:white;border:1px solid #cfdee2;gridline-color:#e4ecee;}
QHeaderView::section{background:#eaf3f5;color:#305865;border:0;padding:9px;}
'''

def password_field():
    edit=QLineEdit(); edit.setEchoMode(QLineEdit.EchoMode.Password); edit.setMaxLength(128)
    edit.setPlaceholderText('請輸入密碼'); return edit

def action(label,callback,layout,primary=False):
    button=QPushButton(label); button.clicked.connect(callback)
    if primary: button.setObjectName('primary')
    layout.addWidget(button); return button

class LoginDialog(QDialog):
    def __init__(self,auth,parent=None):
        super().__init__(parent); self.auth=auth; self.session=None; self.setup=False
        self.setWindowTitle('DrawIQ '+__version__+'｜登入'); self.setMinimumWidth(430); self.setStyleSheet(STYLE)
        box=QVBoxLayout(self); box.setContentsMargins(28,24,28,24); box.setSpacing(12)
        self.title=QLabel(); self.title.setObjectName('loginTitle'); box.addWidget(self.title)
        self.hint=QLabel(); self.hint.setWordWrap(True); self.hint.setObjectName('subtle'); box.addWidget(self.hint)
        self.form=QFormLayout(); self.form.setVerticalSpacing(10)
        self.username=QLineEdit(); self.username.setPlaceholderText('帳號'); self.username.setMaxLength(32)
        self.password=password_field(); self.form.addRow('帳號',self.username); self.form.addRow('密碼',self.password)
        self.confirm=password_field()
        self.form.addRow('確認密碼',self.confirm); box.addLayout(self.form)
        show=QCheckBox('顯示密碼'); show.toggled.connect(self.show_password); box.addWidget(show)
        self.error=QLabel(); self.error.setObjectName('error'); self.error.setWordWrap(True); self.error.hide(); box.addWidget(self.error)
        self.submit_button=action('登入',self.submit,box,True); self.submit_button.setDefault(True)
        self.admin_setup_button=action('',lambda:self.set_admin_setup(not self.setup),box)
        self.admin_setup_button.setAutoDefault(False)
        self.admin_setup_button.setVisible(not auth.has_administrator())
        self.set_admin_setup(False)

    def set_admin_setup(self,enabled):
        self.setup=enabled
        self.title.setText('建立管理者' if enabled else 'DrawIQ 工作台')
        self.hint.setText('管理者密碼使用 15–128 個字元，可用中文或長句。' if enabled else
                          f'首次使用請輸入 {SHARED_ACCOUNT} / {SHARED_ACCOUNT}，設定 4 位數個人帳號和密碼。')
        self.username.setReadOnly(enabled); self.username.setText('admin' if enabled else '')
        self.form.setRowVisible(self.confirm,enabled)
        self.submit_button.setText('建立管理者並登入' if enabled else '登入')
        self.admin_setup_button.setText('返回登入' if enabled else '設定初始管理者')
        self.password.clear(); self.confirm.clear(); self.error.hide()
        (self.password if enabled else self.username).setFocus()
        self.adjustSize()

    def showEvent(self,event):
        super().showEvent(event)
        stage('login.visible',geometry=list(self.geometry().getRect()),first_setup=self.setup)
    def show_password(self,visible):
        mode=QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self.password.setEchoMode(mode); self.confirm.setEchoMode(mode)
    def submit(self):
        self.error.hide(); self.submit_button.setEnabled(False)
        try:
            if self.setup:
                if self.password.text()!=self.confirm.text(): raise ValueError('兩次密碼輸入不同')
                self.auth.bootstrap(self.password.text(),must_change=False)
            session=self.auth.authenticate(self.username.text(),self.password.text())
            if session.registration_only:
                self.password.clear()
                try:
                    dialog=PersonalAccountDialog(self.auth,session,self)
                    if not dialog.exec(): return
                finally:
                    self.auth.logout(session)
                session=dialog.session
            if session.must_change_password:
                dialog=PasswordDialog(self.auth,session,self,initial=True)
                if not dialog.exec():
                    self.auth.logout(session); self.password.clear(); return
                session=dialog.session
            self.auth.require(session); self.session=session; self.password.clear(); self.confirm.clear(); self.accept()
        except (ValueError,RuntimeError) as exc:
            self.error.setText(str(exc)); self.error.show(); self.password.clear(); self.password.setFocus()
        finally: self.submit_button.setEnabled(True)

class PersonalAccountDialog(QDialog):
    def __init__(self,auth,registration,parent=None):
        super().__init__(parent); self.auth=auth; self.registration=registration; self.session=None
        self.setWindowTitle('設定個人帳號'); self.setMinimumWidth(390); self.setStyleSheet(STYLE)
        layout=QVBoxLayout(self); layout.setContentsMargins(24,22,24,22)
        hint=QLabel('帳號和密碼各輸入 4 位數字，設定後直接登入。'); hint.setWordWrap(True); layout.addWidget(hint)
        form=QFormLayout()
        self.username=QLineEdit(); self.password=password_field()
        for field in (self.username,self.password):
            field.setValidator(QRegularExpressionValidator(QRegularExpression('[0-9]{4}'),field))
            field.setPlaceholderText('4 位數字')
        form.addRow('個人帳號',self.username); form.addRow('密碼',self.password); layout.addLayout(form)
        show=QCheckBox('顯示密碼'); show.toggled.connect(self.show_password); layout.addWidget(show)
        self.error=QLabel(); self.error.setObjectName('error'); self.error.setWordWrap(True); self.error.hide(); layout.addWidget(self.error)
        buttons=QHBoxLayout(); self.save_button=action('設定並登入',self.submit,buttons,True)
        self.save_button.setDefault(True); action('取消',self.reject,buttons); layout.addLayout(buttons)

    def show_password(self,visible):
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)

    def submit(self):
        self.save_button.setEnabled(False)
        try:
            self.session=self.auth.register_personal_account(self.registration,self.username.text(),self.password.text())
            self.password.clear(); self.accept()
        except (ValueError,RuntimeError) as exc: self.error.setText(str(exc)); self.error.show()
        finally: self.save_button.setEnabled(True)

class PasswordDialog(QDialog):
    def __init__(self,auth,session,parent=None,initial=False):
        super().__init__(parent); self.auth=auth; self.session=session; self.setStyleSheet(STYLE)
        self.setWindowTitle('首次登入：設定新密碼' if initial else '變更我的密碼'); self.resize(520,380)
        layout=QVBoxLayout(self); layout.setContentsMargins(26,24,26,24)
        heading=QLabel('請更換臨時密碼' if initial else '設定新的登入密碼'); heading.setObjectName('loginTitle'); layout.addWidget(heading)
        pin_allowed=session.role=='user' and is_pin_account(session.username)
        policy='密碼可使用 4 位數字，或 15–128 個字元。' if pin_allowed else '密碼使用 15–128 個字元，可用中文或長句。'
        note=QLabel(session.username+' · '+policy); note.setWordWrap(True); layout.addWidget(note)
        form=QFormLayout(); self.current=password_field(); self.new=password_field(); self.confirm=password_field()
        form.addRow('目前／臨時密碼',self.current); form.addRow('新密碼',self.new); form.addRow('確認新密碼',self.confirm); layout.addLayout(form)
        self.error=QLabel(); self.error.setObjectName('error'); self.error.setWordWrap(True); self.error.hide(); layout.addWidget(self.error)
        row=QHBoxLayout(); self.save_button=action('儲存新密碼',self.submit,row,True); action('取消',self.reject,row); layout.addLayout(row)
    def submit(self):
        self.save_button.setEnabled(False)
        try:
            if self.new.text()!=self.confirm.text(): raise ValueError('兩次新密碼輸入不同')
            self.session=self.auth.change_password(self.session,self.current.text(),self.new.text())
            self.current.clear(); self.new.clear(); self.confirm.clear(); self.accept()
        except (ValueError,RuntimeError) as exc: self.error.setText(str(exc)); self.error.show()
        finally: self.save_button.setEnabled(True)

class UserEditor(QDialog):
    def __init__(self,auth,session,parent=None,username=None):
        super().__init__(parent); self.auth=auth; self.session=session; self.target=username; self.setStyleSheet(STYLE)
        self.setWindowTitle('重設使用者臨時密碼' if username else '新增使用者'); self.resize(560,410)
        layout=QVBoxLayout(self); layout.setContentsMargins(26,24,26,24); form=QFormLayout()
        self.username=QLineEdit(username or ''); self.username.setReadOnly(bool(username)); self.username.setMaxLength(32)
        self.name=QLineEdit(); self.name.setMaxLength(80); self.role=QComboBox()
        for key,label in ROLES.items(): self.role.addItem(label,key)
        self.role.setCurrentIndex(self.role.findData('user'))
        form.addRow('帳號',self.username)
        if not username: form.addRow('使用者姓名',self.name); form.addRow('帳號角色',self.role)
        self.password=QLineEdit(temporary_password()); self.password.setMaxLength(128); form.addRow('臨時密碼',self.password); layout.addLayout(form)
        action('重新產生臨時密碼',lambda:self.password.setText(temporary_password()),layout)
        note=QLabel('請將帳號及臨時密碼交給該使用者。\n使用者首次登入必須更改；儲存後不能查回原密碼。'); note.setWordWrap(True); note.setObjectName('subtle'); layout.addWidget(note)
        self.error=QLabel(); self.error.setObjectName('error'); self.error.setWordWrap(True); self.error.hide(); layout.addWidget(self.error)
        row=QHBoxLayout(); self.save_button=action('儲存',self.submit,row,True); action('取消',self.reject,row); layout.addLayout(row)
    def submit(self):
        self.save_button.setEnabled(False)
        try:
            if self.target: self.auth.reset_password(self.session,self.target,self.password.text())
            else: self.auth.create_user(self.session,self.username.text(),self.name.text(),self.password.text(),self.role.currentData())
            self.password.clear(); self.accept()
        except (ValueError,RuntimeError) as exc: self.error.setText(str(exc)); self.error.show()
        finally:self.save_button.setEnabled(True)

class AccountDialog(QDialog):
    def __init__(self,owner):
        super().__init__(owner); self.owner=owner; self.auth=owner.auth; self.setStyleSheet(STYLE)
        user=self.auth.require(owner.auth_session); self.setWindowTitle('帳號與安全'); self.resize(850,550 if user['role']=='admin' else 280)
        layout=QVBoxLayout(self); layout.setContentsMargins(24,22,24,22)
        heading=QLabel(user['display_name']+' · '+user['username']); heading.setObjectName('loginTitle'); layout.addWidget(heading)
        label=QLabel(ROLES[user['role']]+'｜目前工作區的帳號設定'); layout.addWidget(label)
        action('變更我的密碼',self.change_password,layout)
        self.feedback=QLabel(); self.feedback.setWordWrap(True); layout.addWidget(self.feedback)
        self.users=None
        if user['role']=='admin':
            self.users=QTableWidget(0,5); self.users.setHorizontalHeaderLabels(['帳號','姓名','角色','狀態','密碼'])
            self.users.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            self.users.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.users.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.users.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); layout.addWidget(self.users,1)
            row=QHBoxLayout(); action('新增使用者',self.add_user,row,True); action('重設臨時密碼',self.reset_user,row); action('停用／啟用',self.toggle_user,row); action('登入與帳號歷程',self.show_audit,row); layout.addLayout(row)
            self.refresh()
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
    def run(self,fn):
        try: fn(); self.feedback.setText('已完成'); self.refresh()
        except ValueError as exc: self.feedback.setText(str(exc))
    def refresh(self):
        if self.users is None:return
        rows=self.auth.list_users(self.owner.auth_session); self.users.setRowCount(len(rows))
        for pos,row in enumerate(rows):
            status='啟用' if row['enabled'] else '停用'
            if row['enabled'] and row['locked_until']>self.auth.clock(): status='暫時鎖定'
            for col,val in enumerate((row['username'],row['display_name'],ROLES[row['role']],status,'首次須更改' if row['must_change'] else '已設定')):
                self.users.setItem(pos,col,QTableWidgetItem(val))
    def selected(self):
        if self.users.currentRow()<0: raise ValueError('請先選擇一名使用者')
        return self.users.item(self.users.currentRow(),0).text()
    def change_password(self):
        dialog=PasswordDialog(self.auth,self.owner.auth_session,self)
        if dialog.exec(): self.owner.auth_session=dialog.session; self.feedback.setText('密碼已變更'); self.refresh()
    def add_user(self):
        dialog=UserEditor(self.auth,self.owner.auth_session,self)
        if dialog.exec(): self.refresh(); self.feedback.setText('使用者已建立，下次登入須更改臨時密碼')
    def reset_user(self):
        try:
            name=self.selected()
            if name==self.owner.auth_session.username: raise ValueError('自己的密碼請使用「變更我的密碼」')
            if UserEditor(self.auth,self.owner.auth_session,self,username=name).exec(): self.refresh(); self.feedback.setText('已重設；該帳號既有登入已失效')
        except ValueError as exc:self.feedback.setText(str(exc))
    def toggle_user(self):
        def perform():
            name=self.selected(); row=next(x for x in self.auth.list_users(self.owner.auth_session) if x['username']==name)
            self.auth.set_enabled(self.owner.auth_session,name,not row['enabled'])
        self.run(perform)
    def show_audit(self):
        labels={'bootstrap':'建立初始管理者','login_success':'登入成功','login_failed':'登入失敗','login_blocked':'鎖定期間嘗試登入','logout':'登出',
            'registration_login':'進入個人帳號設定','personal_account_created':'設定個人帳號',
            'user_created':'新增使用者','user_enabled':'啟用使用者','user_disabled':'停用使用者','password_reset':'重設臨時密碼','password_changed':'變更密碼'}
        try:
            rows=self.auth.audit_events(self.owner.auth_session); dialog=QDialog(self); dialog.setWindowTitle('登入與帳號歷程（最近 100 筆）'); dialog.resize(850,500)
            layout=QVBoxLayout(dialog); table=QTableWidget(len(rows),4); table.setHorizontalHeaderLabels(['時間','操作','執行帳號','對象'])
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            for i,row in enumerate(rows):
                for j,value in enumerate((datetime.fromtimestamp(row['at']).strftime('%Y-%m-%d %H:%M:%S'),labels.get(row['action'],row['action']),row['actor'],row['target'])):
                    table.setItem(i,j,QTableWidgetItem(value))
            layout.addWidget(table); buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons); dialog.exec()
        except ValueError as exc:self.feedback.setText(str(exc))

class DesktopSession:
    """Own the window/session lifetime; a logout returns to an unloaded workbench."""
    def __init__(self,app,root): self.app=app; self.root=root; self.auth=AuthService(root); self.window=None; self.login=None
    def present_login(self):
        if self.login is not None and self.login.isVisible():
            self.login.raise_(); self.login.activateWindow()
    def start(self):
        try:
            stage('login.create.start')
            login=LoginDialog(self.auth); self.login=login
            screen=self.app.primaryScreen()
            if screen is not None:
                center=screen.availableGeometry().center()
                login.move(center.x()-login.width()//2,center.y()-login.height()//2)
            QTimer.singleShot(0,self.present_login)
            accepted=bool(login.exec()); self.login=None
            stage('login.closed',accepted=accepted)
            if not accepted: self.app.quit(); return
            from .ui import MainWindow
            stage('workspace.create.start')
            self.window=MainWindow(self.root,auth=self.auth,auth_session=login.session)
            self.window.logout_requested.connect(self.logged_out); self.window.showMaximized()
            self.window.raise_(); self.window.activateWindow()
            stage('workspace.visible',geometry=list(self.window.geometry().getRect()))
            QTimer.singleShot(150,self.window.canvas.fit)
        except Exception as exc:
            failure('desktop.failed')
            self.login=None
            QMessageBox.critical(None,'DrawIQ 啟動錯誤',
                '工作台無法開啟：'+str(exc)+'\n\n請查看工作台資料夾的 launcher.log。')
            self.app.exit(1)
    def logged_out(self):
        self.window=None; QTimer.singleShot(0,self.start)
