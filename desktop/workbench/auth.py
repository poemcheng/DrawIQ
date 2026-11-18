"""Offline workspace accounts. OS file access remains the outer security boundary."""
from contextlib import contextmanager
from dataclasses import dataclass,field
import hashlib,hmac,json,re,secrets,sqlite3,time,unicodedata
from pathlib import Path

ROLES={'admin':'管理者','user':'一般使用者'}
SHARED_ACCOUNT='user'
MIN_PASSWORD_LENGTH=15
SCRYPT_N=2**17
LOCK_SECONDS=300
MAX_FAILURES=5
COMMON_PASSWORDS={'passwordpassword','password123456789','123456789012345','1234567890123456',
                  'qwertyuiopasdfgh','administrator123','drawiqadmin12345'}

class AuthenticationError(ValueError): pass
class SessionExpired(AuthenticationError): pass

@dataclass(frozen=True)
class Session:
    token:str=field(repr=False)
    username:str=''
    display_name:str=''
    role:str='user'
    must_change_password:bool=False
    registration_only:bool=False

def normalize_username(value):
    value=unicodedata.normalize('NFKC',str(value)).strip().casefold()
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{2,31}',value):
        raise ValueError('帳號須為 3–32 個英文字母、數字、點、底線或連字號，首字須為英數字')
    return value

def is_pin_account(username):
    return bool(re.fullmatch(r'[0-9]{4}',username))

def validate_password(password,username='',*,role=None):
    if not isinstance(password,str): raise ValueError('密碼格式不正確')
    password=unicodedata.normalize('NFC',password)
    if role=='user' and is_pin_account(username) and re.fullmatch(r'[0-9]{4}',password):
        return password
    if not MIN_PASSWORD_LENGTH<=len(password)<=128: raise ValueError('密碼請使用 15–128 個字元，可用中文、空格或長句')
    if not password.strip() or len(set(password))<3: raise ValueError('請使用較難猜測的長密碼')
    folded=password.casefold()
    if folded in COMMON_PASSWORDS or (username and folded in {username.casefold()*n for n in range(2,45)}):
        raise ValueError('這組密碼過於常見，請改用不易猜測的長句')
    return password

def hash_password(password):
    salt=secrets.token_bytes(16)
    key=hashlib.scrypt(unicodedata.normalize('NFC',password).encode('utf-8'),salt=salt,n=SCRYPT_N,r=8,p=1,maxmem=256*1024*1024,dklen=32)
    return f'scrypt$17$8$1${salt.hex()}${key.hex()}'

def verify_password(password,encoded):
    if not isinstance(password,str) or len(password)>128: return False
    try:
        algo,power,r,p,salt,expected=encoded.split('$')
        if (algo,power,r,p)!=('scrypt','17','8','1') or len(salt)!=32 or len(expected)!=64: return False
        key=hashlib.scrypt(unicodedata.normalize('NFC',password).encode('utf-8'),salt=bytes.fromhex(salt),
            n=2**int(power),r=int(r),p=int(p),maxmem=256*1024*1024,dklen=32)
        return hmac.compare_digest(key,bytes.fromhex(expected))
    except (ValueError,TypeError): return False

def temporary_password(): return 'DQ-'+secrets.token_urlsafe(18)

class AuthService:
    def __init__(self,root,*,clock=time.time):
        self.root=Path(root).resolve(); self.root.mkdir(parents=True,exist_ok=True)
        self.db=self.root/'auth.sqlite3'; self.clock=clock; self._sessions={}
        with self.connect() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users(
              username TEXT PRIMARY KEY,display_name TEXT NOT NULL,role TEXT NOT NULL,
              password_hash TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,
              must_change INTEGER NOT NULL DEFAULT 1,failures INTEGER NOT NULL DEFAULT 0,
              locked_until REAL NOT NULL DEFAULT 0,credential_version INTEGER NOT NULL DEFAULT 1,
              created_at REAL NOT NULL,updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS auth_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,data TEXT NOT NULL,hash TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS auth_events_no_update BEFORE UPDATE ON auth_events BEGIN SELECT RAISE(ABORT,'auth events are append only'); END;
            CREATE TRIGGER IF NOT EXISTS auth_events_no_delete BEFORE DELETE ON auth_events BEGIN SELECT RAISE(ABORT,'auth events are append only'); END;
            ''')
        # Equal-cost work for unknown usernames. This is not an account or a valid login.
        self._dummy='scrypt$17$8$1$'+'0'*32+'$'+'0'*64

    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.db,timeout=10); c.row_factory=sqlite3.Row
        try:
            with c: yield c
        finally: c.close()

    def _event(self,c,action,actor,target='',**details):
        prev=c.execute('SELECT hash FROM auth_events ORDER BY seq DESC LIMIT 1').fetchone()
        data=json.dumps({'at':self.clock(),'action':action,'actor':actor,'target':target,'details':details,
            'previous_hash':prev['hash'] if prev else None},ensure_ascii=False,sort_keys=True)
        c.execute('INSERT INTO auth_events(data,hash) VALUES(?,?)',(data,hashlib.sha256(data.encode()).hexdigest()))

    def has_accounts(self):
        with self.connect() as c: return c.execute('SELECT 1 FROM users LIMIT 1').fetchone() is not None

    def has_administrator(self):
        with self.connect() as c:
            return c.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone() is not None

    def _insert(self,c,username,display_name,password,role,must_change):
        username=normalize_username(username); password=validate_password(password,username,role=role)
        if role not in ROLES: raise ValueError('未知帳號角色')
        display_name=display_name.strip()
        if not display_name or len(display_name)>80: raise ValueError('請填 1–80 個字元的使用者姓名')
        if c.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone(): raise ValueError('此帳號已存在')
        now=self.clock()
        c.execute('INSERT INTO users(username,display_name,role,password_hash,must_change,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
            (username,display_name,role,hash_password(password),int(must_change),now,now))
        return username

    def bootstrap(self,password,display_name='系統管理者',*,must_change=True):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
                raise ValueError('管理者已建立，不能重做初始設定')
            name=self._insert(c,'admin',display_name,password,'admin',must_change)
            self._event(c,'bootstrap','local_setup',name)
        return name

    def register_personal_account(self,session,username,password):
        username=unicodedata.normalize('NFKC',str(username)).strip()
        if not is_pin_account(username) or not isinstance(password,str) or not re.fullmatch(r'[0-9]{4}',password):
            raise ValueError('個人帳號和密碼均須為 4 位數字（0–9）')
        token=getattr(session,'token',None)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if self._sessions.get(token)!=(SHARED_ACCOUNT,None):
                raise AuthenticationError('請先用共用帳號登入以設定個人帳號')
            name=self._insert(c,username,'使用者 '+username,password,'user',False)
            self._event(c,'personal_account_created',SHARED_ACCOUNT,name,role='user')
            self._sessions.pop(token,None)
        return self._start_session(name,'使用者 '+name,'user',1)

    def _start_session(self,username,display_name,role,credential_version,must_change=False):
        token=secrets.token_urlsafe(32)
        self._sessions[token]=(username,credential_version)
        return Session(token,username,display_name,role,must_change,credential_version is None)

    def authenticate(self,username,password):
        try: username=normalize_username(username)
        except ValueError: username='invalid'
        if username==SHARED_ACCOUNT and password==SHARED_ACCOUNT:
            with self.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                self._event(c,'registration_login',SHARED_ACCOUNT)
            return self._start_session(SHARED_ACCOUNT,'共用帳號','user',None)
        failure=False; row=None
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
            now=self.clock()
            if row and row['locked_until']>now:
                self._event(c,'login_blocked',username,username); failure=True
            else:
                valid=verify_password(password,row['password_hash'] if row else self._dummy)
                if not row or not row['enabled'] or not valid:
                    if row and row['enabled']:
                        count=(0 if row['locked_until'] else row['failures'])+1
                        c.execute('UPDATE users SET failures=?,locked_until=? WHERE username=?',
                            (count,now+LOCK_SECONDS if count>=MAX_FAILURES else 0,username))
                    self._event(c,'login_failed',username,username); failure=True
                else:
                    c.execute('UPDATE users SET failures=0,locked_until=0 WHERE username=?',(username,))
                    self._event(c,'login_success',username,username)
        # Raise after committing failed-attempt accounting, not inside its transaction.
        if failure: raise AuthenticationError('登入未成功。請確認帳號、密碼及啟用狀態；連續 5 次失敗須等候 5 分鐘。')
        return self._start_session(username,row['display_name'],row['role'],row['credential_version'],bool(row['must_change']))

    def _require(self,c,session,*,admin=False,allow_change=False):
        entry=self._sessions.get(getattr(session,'token',None))
        if not entry: raise SessionExpired('登入已失效，請重新登入')
        if entry[1] is None: raise AuthenticationError('共用帳號僅供設定個人帳號')
        row=c.execute('SELECT * FROM users WHERE username=?',(entry[0],)).fetchone()
        if not row or not row['enabled'] or row['credential_version']!=entry[1]:
            self._sessions.pop(session.token,None); raise SessionExpired('帳號狀態已變更，請重新登入')
        if row['must_change'] and not allow_change: raise AuthenticationError('首次登入請先更改臨時密碼')
        if admin and row['role']!='admin': raise AuthenticationError('此操作需要管理者權限')
        return row

    def require(self,session,*,admin=False,allow_change=False):
        with self.connect() as c:
            row=self._require(c,session,admin=admin,allow_change=allow_change)
            return {k:row[k] for k in ('username','display_name','role','enabled','must_change')}

    def actor(self,session):
        row=self.require(session)
        return row['username']+' ('+row['display_name']+')'

    def logout(self,session):
        entry=self._sessions.pop(getattr(session,'token',None),None)
        if entry:
            with self.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                self._event(c,'logout',entry[0],entry[0])

    def create_user(self,session,username,display_name,password,role='user'):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE'); actor=self._require(c,session,admin=True)
            name=self._insert(c,username,display_name,password,role,True)
            self._event(c,'user_created',actor['username'],name,role=role)
        return name

    def list_users(self,session):
        with self.connect() as c:
            self._require(c,session,admin=True)
            return [dict(r) for r in c.execute('SELECT username,display_name,role,enabled,must_change,failures,locked_until FROM users ORDER BY username')]

    def set_enabled(self,session,username,enabled):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE'); actor=self._require(c,session,admin=True)
            target=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
            if not target: raise ValueError('找不到使用者')
            if not enabled and username==actor['username']: raise ValueError('不能停用目前登入的管理者')
            if not enabled and target['role']=='admin' and c.execute("SELECT count(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0]<=1:
                raise ValueError('必須保留至少一名啟用的管理者')
            if bool(target['enabled'])==bool(enabled): return
            c.execute('UPDATE users SET enabled=?,credential_version=credential_version+1,updated_at=? WHERE username=?',(int(bool(enabled)),self.clock(),username))
            self._event(c,'user_enabled' if enabled else 'user_disabled',actor['username'],username)

    def reset_password(self,session,username,password):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE'); actor=self._require(c,session,admin=True)
            if username==actor['username']: raise ValueError('自己的密碼請使用「變更我的密碼」')
            target=c.execute('SELECT role FROM users WHERE username=?',(username,)).fetchone()
            if not target: raise ValueError('找不到使用者')
            password=validate_password(password,username,role=target['role'])
            c.execute('UPDATE users SET password_hash=?,must_change=1,failures=0,locked_until=0,credential_version=credential_version+1,updated_at=? WHERE username=?',
                (hash_password(password),self.clock(),username))
            self._event(c,'password_reset',actor['username'],username)

    def change_password(self,session,current_password,new_password):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE'); row=self._require(c,session,allow_change=True)
            new_password=validate_password(new_password,row['username'],role=row['role'])
            if not verify_password(current_password,row['password_hash']): raise AuthenticationError('目前密碼不正確')
            if verify_password(new_password,row['password_hash']): raise ValueError('新密碼不可與目前密碼相同')
            c.execute('UPDATE users SET password_hash=?,must_change=0,failures=0,locked_until=0,credential_version=credential_version+1,updated_at=? WHERE username=?',
                (hash_password(new_password),self.clock(),row['username']))
            self._event(c,'password_changed',row['username'],row['username'])
            generation=row['credential_version']+1
        self._sessions.pop(session.token,None)
        return self._start_session(row['username'],row['display_name'],row['role'],generation)

    def audit_events(self,session,limit=100):
        with self.connect() as c:
            self._require(c,session,admin=True)
            return [json.loads(r['data']) for r in c.execute('SELECT data FROM auth_events ORDER BY seq DESC LIMIT ?',(min(max(int(limit),1),500),))]

def authenticate_cli(root,username=None):
    import getpass,sys
    service=AuthService(root)
    if not service.has_accounts(): raise AuthenticationError('請先啟動圖形介面設定個人帳號或管理者')
    if not sys.stdin.isatty(): raise AuthenticationError('CLI 需要互動登入；自動唯讀整合請使用具獨立權杖的本機 API')
    username=username or input('DrawIQ 帳號：')
    session=service.authenticate(username,getpass.getpass('密碼：'))
    service.require(session)
    return service,session
