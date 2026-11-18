import base64, hashlib, json, os, secrets, sqlite3, threading, time
from dataclasses import asdict
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
import requests
from dotenv import load_dotenv
from engine import analyze, norm, period, period_options, today_jalali, rows_at, vacation_query, coverage_dates, path, jalali, vacation_line, load_holidays

ROOT=Path(__file__).resolve().parent
load_dotenv(ROOT/'.env')
CFG=json.loads((ROOT/os.getenv('CONFIG_FILE','config.json')).read_text(encoding='utf-8'))
CFG['holidays']=load_holidays(ROOT/CFG.get('holiday_calendar_file','holidays.json'))
BASE=os.getenv('WEBKART_BASE_URL','http://192.168.1.49:8081/').rstrip('/')
FRONT=os.getenv('WEBKART_FRONTEND','http://192.168.1.49:8080/').rstrip('/')+'/'
PUBLIC=os.getenv('PUBLIC_LOGIN_URL','').rstrip('/')
TOKEN=os.getenv('BALE_BOT_TOKEN','')
WRITES=os.getenv('ENABLE_WRITES','false').lower()=='true'
DB=ROOT/os.getenv('STATE_DB','state.sqlite3')
SESSION_TTL=int(os.getenv('SESSION_MINUTES','60'))*60
PAIR_TTL=int(os.getenv('PAIR_MINUTES','5'))*60
LOCK=threading.RLock()
PAIRS={}; CHAT_LOGINS={}; USERS={}; SELECTED_PERIODS={}; PROPOSALS={}

class WebError(Exception): pass
class Uncertain(WebError): pass

class WebKart:
    def __init__(self):
        u=urlparse(BASE)
        if u.scheme!='https' and not (u.scheme=='http' and u.hostname=='192.168.1.49'):
            raise WebError('ارتباط خارجی HTTP مجاز نیست؛ VPN یا HTTPS لازم است')
        self.http=requests.Session(); self.http.trust_env=False
        self.http.headers.update({'Accept':'application/json, text/plain, */*','Origin':FRONT.rstrip('/'),'Referer':FRONT,'token':'','username':''})
        self.person=None
    def call(self, method, endpoint, expected=1, label=None, **kwargs):
        if '://' in endpoint or endpoint.startswith('//'): raise WebError('آدرس API نامعتبر')
        label=label or 'WebKart'
        try:
            r=self.http.request(method,BASE+'/'+endpoint.lstrip('/'),timeout=(5,25),allow_redirects=False,**kwargs)
            if not 200<=r.status_code<300: raise ValueError('HTTP')
            obj=r.json()
            if not isinstance(obj,dict) or 'ResponseResultType' not in obj: raise ValueError('JSON')
        except (requests.RequestException, ValueError):
            raise Uncertain(f'پاسخ معتبر از بخش «{label}» دریافت نشد') from None
        if norm(obj['ResponseResultType'])!=str(expected):
            code=norm(obj['ResponseResultType'])[:12]
            message=' '.join(norm(obj.get('Message')).split())[:160]
            detail=f': {message}' if message else ''
            raise WebError(f'بخش «{label}» توسط سامانه تأیید نشد (کد {code}{detail})')
        return obj
    def login(self,person,password):
        obj=self.call('POST','NewLoginPersonel',expected=9,label='ورود',data={'username':person,'password':base64.b64encode(password.encode()).decode(),'loginType':0})
        token=obj.get('Values')
        if not isinstance(token,str) or not token: raise WebError('توکن ورود دریافت نشد')
        self.http.headers.update({'token':token,'username':person})
        profile=self.call('GET','GetEosPersonelDetail',label='مشخصات پرسنلی',params={'perno':person}).get('Values')
        if not isinstance(profile,dict) or norm(profile.get('Perno'))!=person: raise WebError('تطبیق کد پرسنلی ناموفق بود')
        self.person=person
        return norm(profile.get('PersonName'))
    def vacation_list(self,start,end,search_kind=None):
        ec=CFG['existing_requests']
        if not ec.get('endpoint'): raise WebError('API فهرست مرخصی تنظیم نشده')
        return self.call('GET',ec['endpoint'],label='فهرست مرخصی‌ها',params=vacation_query(ec,start,end,self.person,search_kind))
    def snapshot(self,start=None,end=None):
        today=today_jalali()
        if start is None or end is None: start,end=period(today)
        work=self.call('GET','GetPersonDayWorksTotal',label='کارکرد دوره',params={'begindate':start,'enddate':end,'perno':self.person})
        io_cfg=CFG['io']; io_endpoint=io_cfg.get('endpoint')
        if not io_endpoint: raise WebError('مسیر API تردد دوره‌ای تنظیم نشده است')
        io=self.call('GET',io_endpoint,label='تردد دوره',params={'beginDate':start,'endDate':end,'perno':self.person})
        days=rows_at(work,'Values.WebPersonDayWorks.WebWorkDayList')
        if any(norm(d.get('PERNO'))!=self.person for d in days): raise WebError('عدم تطبیق مالک رکورد کارکرد')
        io_rows=rows_at(io,io_cfg['rows_path'])
        person_field=io_cfg.get('person_field')
        if person_field and any(norm(path(row,person_field))!=self.person for row in io_rows):
            raise WebError('عدم تطبیق مالک رکورد تردد')
        ec=CFG['existing_requests']; existing=None; pending=None
        if ec.get('endpoint') and ec.get('rows_path') is not None:
            existing=self.vacation_rows(start,end)
            pending=self.vacation_rows(start,end,1)
        return today,start,end,work,io,days,existing,pending
    def vacation_rows(self,start,end,kind=None):
        ec=CFG['existing_requests']
        if ec.get('rows_path') is None or not ec.get('begin_date_field') or not ec.get('end_date_field'):
            raise WebError('ساختار پاسخ لیست مرخصی هنوز تنظیم نشده است')
        obj=self.vacation_list(start,end,kind)
        rows=rows_at(obj,ec['rows_path'])
        coverage_dates(rows,ec,respect_ignored=False)
        count=norm(obj.get('TotalRow'))
        if count.isdigit() and int(count)>len(rows):
            raise WebError('لیست مرخصی ناقص است؛ صفحه‌بندی API باید بررسی شود')
        person_key=ec.get('person_field')
        if person_key and any(norm(path(r,person_key))!=self.person for r in rows):
            raise WebError('عدم تطبیق مالک درخواست مرخصی')
        return rows
    def register(self,p):
        payload={'BeginDate':p.date,'EndDate':p.date,'BeginTime':p.start,'EndTime':p.end,'Description':'','VacationType':CFG['leave_type']['id'],'PageNo':'','PersonnelNo':self.person}
        encoding=CFG['add_vacation_encoding']
        if encoding not in ('form','json'): raise WebError('نوع ارسال AddVacation نامعتبر')
        return self.call('POST','AddVacation',label='ثبت مرخصی',**({'data':payload} if encoding=='form' else {'json':payload}))
    def close(self): self.http.close()

class Bale:
    def call(self,method,**data):
        try:
            r=requests.post(f'https://tapi.bale.ai/bot{TOKEN}/{method}',json=data,timeout=40 if method=='getUpdates' else 15,allow_redirects=False)
            if not 200<=r.status_code<300: raise ValueError()
            o=r.json()
            if not o.get('ok'): raise ValueError()
            return o.get('result')
        except (requests.RequestException,ValueError): raise RuntimeError('Bale API error') from None
    def say(self,chat,text,buttons=None):
        kw={'chat_id':chat,'text':text}
        if buttons: kw['reply_markup']={'inline_keyboard':buttons}
        return self.call('sendMessage',**kw)

BALE=Bale()

def button(text,data): return {'text':text,'callback_data':data}
def selected_period(chat): return SELECTED_PERIODS.get(chat,period(today_jalali()))
def period_id(date_range): return date_range[0][:7].replace('/','-')
def menu(chat,text='عملیات موردنظر را انتخاب کنید:'):
    start,end=selected_period(chat)
    BALE.say(chat,text+f'\nدوره فعال: {start} تا {end}',[[button('ورود / اتصال حساب','login')],[button('📅 انتخاب دوره','periods')],[button('بررسی تأخیرهای دوره','scan')],[button('خلاصه کارکرد','summary')],[button('وضعیت درخواست‌های مرخصی','vacations')],[button('خروج از حساب','logout')]])

def period_menu(chat):
    active=selected_period(chat)
    rows=[]
    for item in period_options(today_jalali()):
        label=('✅ ' if item==active else '')+item[0]+' تا '+item[1]
        rows.append([button(label,'prd:'+period_id(item))])
    rows.append([button('بازگشت','/menu')])
    BALE.say(chat,'دوره موردنظر را انتخاب کنید:',rows)

def select_period(chat,value):
    options={period_id(item):item for item in period_options(today_jalali())}
    if value not in options: raise WebError('دوره انتخابی معتبر یا در دسترس نیست')
    SELECTED_PERIODS[chat]=options[value]
    PROPOSALS.pop(chat,None)
    start,end=options[value]
    menu(chat,f'دوره {start} تا {end} انتخاب شد.')

def db(): return sqlite3.connect(DB,timeout=10)
def init_db():
    with db() as c: c.execute('CREATE TABLE IF NOT EXISTS submissions (key TEXT PRIMARY KEY, status TEXT NOT NULL, created_at INTEGER NOT NULL)')
    os.chmod(DB,0o600)
def claim(person,p):
    # One request per personnel/date across restarts, even if interval later changes.
    k=hashlib.sha256(f'{person}|{p.date}'.encode()).hexdigest()
    with db() as c:
        cur=c.execute('INSERT OR IGNORE INTO submissions VALUES (?, ?, ?)',(k,'pending',int(time.time())))
        return k if cur.rowcount==1 else None

def mark(k,status):
    with db() as c: c.execute('UPDATE submissions SET status=? WHERE key=?',(status,k))

def cleanup():
    now=time.monotonic()
    with LOCK:
        for key,v in list(PAIRS.items()):
            if v[1]<now: PAIRS.pop(key,None)
        for chat,v in list(CHAT_LOGINS.items()):
            if v['expiry']<now: CHAT_LOGINS.pop(chat,None)
        for chat,(client,expiry) in list(USERS.items()):
            if expiry<now:
                USERS.pop(chat,None); PROPOSALS.pop(chat,None); client.close()
        for chat,v in list(PROPOSALS.items()):
            if v['expiry']<now: PROPOSALS.pop(chat,None)

def get_client(chat):
    cleanup()
    item=USERS.get(chat)
    if item is None: raise WebError('ابتدا از منوی ورود، حساب خود را متصل کنید')
    return item[0]

def logout(chat):
    with LOCK:
        item=USERS.pop(chat,None); PROPOSALS.pop(chat,None); CHAT_LOGINS.pop(chat,None); SELECTED_PERIODS.pop(chat,None)
        for key,v in list(PAIRS.items()):
            if v[0]==chat: PAIRS.pop(key,None)
        if item: item[0].close()

def activate_session(chat,client,name):
    with LOCK:
        logout(chat); USERS[chat]=(client,time.monotonic()+SESSION_TTL)
    try:
        menu(chat,'ورود موفق شد. '+name)
        with LOCK:
            try: scan(chat,get_client(chat))
            except (WebError,ValueError) as e: BALE.say(chat,str(e))
    except RuntimeError:
        pass

def start_chat_login(chat):
    cleanup()
    with LOCK:
        CHAT_LOGINS[chat]={'stage':'username','username':None,'tries':0,'expiry':time.monotonic()+PAIR_TTL}
        for key,v in list(PAIRS.items()):
            if v[0]==chat: PAIRS.pop(key,None)
    BALE.say(chat,'کد پرسنلی خود را ارسال کنید.',[[button('انصراف','login:cancel')]])

def handle_chat_login(chat,text,message_id):
    cleanup()
    with LOCK:
        state=CHAT_LOGINS.get(chat)
        if not state: raise WebError('درخواست ورود منقضی شده است؛ دوباره ورود را انتخاب کنید')
        stage=state['stage']
    if stage=='username':
        person=norm(text)
        if not person.isascii() or not person.isdigit() or len(person)>20:
            BALE.say(chat,'کد پرسنلی نامعتبر است؛ فقط عدد ارسال کنید.',[[button('انصراف','login:cancel')]])
            return
        with LOCK:
            state=CHAT_LOGINS.get(chat)
            if not state: raise WebError('درخواست ورود منقضی شده است')
            state.update(stage='password',username=person,expiry=time.monotonic()+PAIR_TTL)
        BALE.say(chat,'حالا رمز عبور را ارسال کنید. ربات بلافاصله درخواست حذف پیام رمز را به بله می‌فرستد.',[[button('انصراف','login:cancel')]])
        return
    if stage!='password':
        raise WebError('وضعیت ورود نامعتبر است؛ دوباره تلاش کنید')
    deleted=False
    if message_id is not None:
        try:
            BALE.call('deleteMessage',chat_id=chat,message_id=message_id); deleted=True
        except RuntimeError:
            pass
    if not deleted:
        BALE.say(chat,'حذف خودکار پیام رمز ممکن نشد؛ لطفاً پیام را دستی حذف کنید.')
    password=text if isinstance(text,str) else ''
    if not password or len(password)>256:
        del password
        BALE.say(chat,'رمز عبور نامعتبر است؛ دوباره ارسال کنید.',[[button('انصراف','login:cancel')]])
        return
    with LOCK:
        state=CHAT_LOGINS.get(chat)
        if not state or state['stage']!='password':
            del password
            raise WebError('درخواست ورود منقضی شده است')
        person=state['username']; state['tries']+=1; tries=state['tries']
    client=WebKart()
    try:
        name=client.login(person,password)
    except (WebError,ValueError):
        client.close()
        with LOCK:
            if tries>=3: CHAT_LOGINS.pop(chat,None)
        if tries>=3:
            menu(chat,'ورود تأیید نشد و تعداد تلاش مجاز تمام شد.')
        else:
            BALE.say(chat,'ورود تأیید نشد؛ رمز را دوباره ارسال کنید.',[[button('انصراف','login:cancel')]])
        return
    finally:
        del password
    with LOCK:
        state=CHAT_LOGINS.get(chat)
        if not state or state['expiry']<time.monotonic():
            client.close(); CHAT_LOGINS.pop(chat,None)
            raise WebError('درخواست ورود منقضی شد؛ دوباره تلاش کنید')
        CHAT_LOGINS.pop(chat,None)
    activate_session(chat,client,name)

def start_pair(chat):
    cleanup()
    if urlparse(PUBLIC).scheme!='https' or not urlparse(PUBLIC).netloc:
        raise WebError('مدیر باید نشانی HTTPS فرم ورود را تنظیم کند')
    with LOCK:
        for k,v in list(PAIRS.items()):
            if v[0]==chat: PAIRS.pop(k,None)
        key=secrets.token_urlsafe(32); PAIRS[key]=(chat,time.monotonic()+PAIR_TTL,0)
    BALE.say(chat,'برای ورود، فرم امن را باز کنید. رمز را در گفتگو ارسال نکنید.',[[{'text':'فرم ورود امن','url':PUBLIC+'/login/'+key}]])

PAGE='''<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>ورود حضور و غیاب</title><style>body{font:16px Tahoma, sans-serif;background:#f3f5f7;color:#172b35;margin:0;padding:25px}main{max-width:390px;margin:8vh auto;background:white;padding:26px;border-radius:16px}input,button{box-sizing:border-box;width:100%;padding:12px;margin:8px 0;border:1px solid #bbc7cc;border-radius:8px;font:inherit}button{color:white;background:#126b56;border:0}small{color:#53636b}</style><main><h2>ورود به WebKart</h2><p>{{ message }}</p>{% if show %}<form method="post"><input name="username" placeholder="کد پرسنلی" inputmode="numeric" autocomplete="username" required maxlength="20"><input name="password" type="password" placeholder="رمز عبور" autocomplete="current-password" required maxlength="256"><button>ورود و اتصال حساب بله</button></form><small>رمز عبور ذخیره نمی‌شود. پس از ورود، به بله برگردید.</small>{% endif %}</main></html>'''

def app_factory():
    from flask import Flask, request, render_template_string, abort
    app=Flask(__name__); app.config['MAX_CONTENT_LENGTH']=4096
    @app.after_request
    def protect(r):
        r.headers['Cache-Control']='no-store'; r.headers['Referrer-Policy']='no-referrer'; r.headers['X-Frame-Options']='DENY'; r.headers['X-Content-Type-Options']='nosniff'; r.headers['Content-Security-Policy']="default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        return r
    def page(msg,show=False,status=200): return render_template_string(PAGE,message=msg,show=show),status
    @app.get('/')
    def index():
        return page('برای ورود، دکمه «ورود / اتصال حساب» را در ربات بله انتخاب کنید.')
    @app.route('/login/<key>',methods=['GET','POST'])
    @app.route('/webkart-auth/login/<key>',methods=['GET','POST'])
    def login(key):
        cleanup()
        # Bind to one-time secret link; trust HTTPS termination only on loopback proxy.
        with LOCK:
            item=PAIRS.get(key)
            if not item: return page('لینک نامعتبر یا منقضی است.',False,410)
            if request.method=='GET': return page('اطلاعات حساب شخصی خود را وارد کنید.',True)
            if request.headers.get('Origin') and request.headers['Origin']!=f'{urlparse(PUBLIC).scheme}://{urlparse(PUBLIC).netloc}': abort(403)
            chat,expiry,tries=item
            if tries>=3: PAIRS.pop(key,None); return page('تعداد تلاش بیش از حد مجاز.',False,429)
            PAIRS[key]=(chat,expiry,tries+1)
        person=norm(request.form.get('username')); password=request.form.get('password','')
        if not person.isascii() or not person.isdigit() or not password: return page('اطلاعات ورود نامعتبر.',True,400)
        client=WebKart()
        try: name=client.login(person,password)
        except WebError:
            client.close(); return page('ورود تأیید نشد؛ اطلاعات یا اتصال را بررسی کنید.',True,401)
        finally: del password
        with LOCK:
            active=PAIRS.pop(key,None)
            if not active or active[1]<time.monotonic(): client.close(); return page('لینک منقضی شد.',False,410)
        activate_session(chat,client,name)
        return page('ورود موفق شد. به ربات بله برگردید.')
    return app

def scan(chat,client):
    start,end=selected_period(chat)
    today,_,_,work,io,days,existing,pending=client.snapshot(start,end)
    proposals,skipped=analyze(days,io,CFG,today,existing,pending,today_time=datetime.now(ZoneInfo('Asia/Tehran')).strftime('%H:%M'),date_range=(start,end))
    PROPOSALS.pop(chat,None)
    cutoff=min(end,f'{today[0]:04d}/{today[1]:02d}/{today[2]:02d}')
    note=f'دوره: {start} تا {end}\nبررسی تا {cutoff}؛ {len(proposals)} پیشنهاد، {len(skipped)} مورد نیازمند بررسی دستی.'
    if not WRITES or existing is None or not CFG['leave_type'].get('confirmed_hourly'):
        note+='\nحالت پیش‌نمایش: ثبت واقعی هنوز فعال نیست.'
    BALE.say(chat,note)
    for i in range(0,len(skipped),15):
        BALE.say(chat,'موارد نیازمند بررسی:\n'+'\n'.join(f'{d}: {r}' for d,r in skipped[i:i+15]))
    if not proposals: return
    batch=secrets.token_hex(5)
    PROPOSALS[chat]={'batch':batch,'items':proposals,'selected':set(),'period':(start,end),'expiry':time.monotonic()+900}
    show_selection(chat)

def show_selection(chat):
    v=PROPOSALS[chat]; selected=v['selected']; items=v['items']; b=v['batch']
    text='مرخصی‌های ساعتی پیشنهادی (انتخاب کنید):\n'+'\n'.join(f'{"✅" if i in selected else "◻️"} {p.date} {p.day} | {p.start} تا {p.end} | {p.mins} دقیقه | {"عدم ثبت ورود و خروج (تمام شیفت)" if p.kind == "no_punches" else "تأخیر ورود"}' for i,p in enumerate(items))
    text+=f'\nانتخاب‌شده: {len(selected)} | {sum(items[i].mins for i in selected)} دقیقه'
    buttons=[[button(('✅ ' if i in selected else '◻️ ')+p.date,f't:{b}:{i}')] for i,p in enumerate(items)]
    buttons += [[button('انتخاب همه',f'all:{b}'),button('حذف انتخاب',f'none:{b}')],[button('بررسی نهایی و ثبت',f'confirm:{b}')],[button('انصراف',f'cancel:{b}')]]
    BALE.say(chat,text,buttons)

def summary(chat,client):
    start,end=selected_period(chat)
    w=client.call('GET','GetPersonDayWorksTotal',label='خلاصه کارکرد',params={'begindate':start,'enddate':end,'perno':client.person})
    t=w['Values']['TotallPersonDayWork']
    BALE.say(chat,f'دوره {start} تا {end}\nکارکرد: {t.get("TotalWork","-")}\nکسری: {t.get("TotalKasrKar","-")}\nاضافه‌کاری: {t.get("TotalEzafeKar","-")}\nمرخصی: {t.get("TotalMorakhasi","-")}\nمأموریت: {t.get("TotalMission","-")}')

VACATION_STATUS_LABELS={1:'در حال پیگیری (مرخصی تایید نشده)',2:'تایید شده',3:'پذیرفته نشده',4:'برگشت خورده',5:'تمامی درخواست‌ها'}

def vacation_menu(chat):
    start,end=selected_period(chat)
    BALE.say(chat,f'وضعیت درخواست‌های مرخصی دوره {start} تا {end} را انتخاب کنید:',
             [[button(label,f'vac:{kind}')] for kind,label in VACATION_STATUS_LABELS.items()]+[[button('بازگشت','/menu')]])

def show_vacations(chat,client,kind):
    start,end=selected_period(chat); ec=CFG['existing_requests']
    # Show the actual full-list records, including any unrecognized status.
    rows=client.vacation_rows(start,end,None if kind==5 else kind)
    BALE.say(chat,f'درخواست‌های مرخصی دوره {start} تا {end}\n'+VACATION_STATUS_LABELS[kind]+f' | {len(rows)} درخواست')
    if not rows: BALE.say(chat,'موردی یافت نشد')
    lines=[vacation_line(r,ec) for r in rows]
    for i in range(0,len(lines),15): BALE.say(chat,'\n'.join(lines[i:i+15]))
    vacation_menu(chat)

def submit(chat,client,v):
    if not v['selected']: raise WebError('هیچ موردی انتخاب نشده است')
    if not WRITES or not CFG['leave_type'].get('confirmed_hourly') or not CFG['existing_requests'].get('endpoint'):
        raise WebError('ثبت واقعی غیرفعال است؛ مدیر باید نوع مرخصی و API درخواست قبلی را تأیید کند')
    # Verify configured type really appears in the user's available type list; schema is explicit.
    lc=CFG['leave_type']
    if lc.get('rows_path') is None or not lc.get('id_field'): raise WebError('ساختار فهرست انواع مرخصی تنظیم نشده')
    types=rows_at(client.call('GET','Vacation/GetWebVacationTypeList',label='انواع مرخصی'),lc['rows_path'])
    from engine import path
    if not any(norm(path(r,lc['id_field']))==norm(lc['id']) for r in types): raise WebError('نوع مرخصی مجاز یافت نشد')
    selected=[v['items'][i] for i in sorted(v['selected'])]; PROPOSALS.pop(chat,None)
    results=[]
    for p in selected:
        # Fetch immediately before each POST; never retry an ambiguous write.
        try:
            start,end=v.get('period') or selected_period(chat)
            today,_,_,_,io,days,existing,pending=client.snapshot(start,end)
            if existing is None or pending is None: raise WebError('درخواست‌های قبلی قابل بررسی نیست')
            current,review=analyze(days,io,CFG,today,existing,pending,today_time=datetime.now(ZoneInfo('Asia/Tehran')).strftime('%H:%M'),date_range=(start,end))
            if p not in current:
                reason=next((r for d,r in review if d==p.date),'تغییر کارکرد / ثبت نشد')
                results.append(f'{p.date}: {reason}'); continue
            k=claim(client.person,p)
            if not k: results.append(f'{p.date}: قبلاً اقدام شده / بررسی دستی'); continue
            try:
                client.register(p); mark(k,'success'); results.append(f'{p.date}: ✅ درخواست ثبت شد؛ در انتظار تعیین وضعیت در سامانه')
            except Uncertain:
                mark(k,'uncertain'); results.append(f'{p.date}: نتیجه نامشخص؛ در سامانه بررسی کنید')
            except WebError:
                mark(k,'rejected'); results.append(f'{p.date}: سامانه تأیید نکرد؛ بررسی دستی')
        except (WebError,ValueError): results.append(f'{p.date}: بررسی مجدد ناموفق؛ ثبت نشد')
    BALE.say(chat,'نتیجه ثبت درخواست‌ها:\n'+'\n'.join(results))

def dispatch(chat,action,message_id=None,is_callback=False):
    action=action if isinstance(action,str) else ''
    if action in ('/start','/menu'):
        with LOCK: CHAT_LOGINS.pop(chat,None)
        menu(chat); return
    if action=='login': start_chat_login(chat); return
    if action in ('/cancel','login:cancel'):
        with LOCK: CHAT_LOGINS.pop(chat,None)
        menu(chat,'ورود لغو شد.'); return
    if action in ('logout','/logout'): logout(chat); menu(chat,'از حساب خارج شدید.'); return
    with LOCK: chat_login=chat in CHAT_LOGINS
    if chat_login:
        if is_callback:
            BALE.say(chat,'برای ادامه، مقدار خواسته‌شده را به صورت پیام ارسال کنید یا «انصراف» را بزنید.')
        else:
            handle_chat_login(chat,action,message_id)
        return
    if action not in ('periods','scan','summary','vacations') and not action.startswith(('prd:','vac:','t:','all:','none:','confirm:','submit:','cancel:')):
        menu(chat,'عملیات را از منو انتخاب کنید.'); return
    with LOCK:
        client=get_client(chat)
        if action=='periods': period_menu(chat); return
        if action.startswith('prd:'): select_period(chat,action.split(':',1)[1]); return
        if action=='scan': scan(chat,client); return
        if action=='summary': summary(chat,client); return
        if action=='vacations': vacation_menu(chat); return
        if action.startswith('vac:'):
            raw=action.split(':',1)[1]
            if raw not in ('1','2','3','4','5'): raise WebError('وضعیت نامعتبر')
            show_vacations(chat,client,int(raw)); return
        parts=action.split(':'); v=PROPOSALS.get(chat)
        if not v or len(parts)<2 or parts[1]!=v['batch']: raise WebError('فهرست منقضی شده؛ دوباره بررسی کنید')
        cmd=parts[0]
        if cmd=='cancel': PROPOSALS.pop(chat,None); menu(chat,'لغو شد'); return
        if cmd=='confirm':
            if not v['selected']: raise WebError('ابتدا موارد را انتخاب کنید')
            v['confirm']=secrets.token_hex(5)
            BALE.say(chat,'آیا ثبت '+str(len(v['selected']))+' درخواست انتخابی را تأیید می‌کنید؟',[[button('ثبت نهایی',f'submit:{v["batch"]}:{v["confirm"]}'),button('انصراف',f'cancel:{v["batch"]}')]])
            return
        if cmd=='submit':
            if len(parts)!=3 or parts[2]!=v.get('confirm'): raise WebError('تأیید معتبر نیست')
            v.pop('confirm',None); submit(chat,client,v); return
        v.pop('confirm',None)
        if cmd=='t' and len(parts)==3 and parts[2].isdigit() and int(parts[2])<len(v['items']):
            i=int(parts[2]); v['selected'].symmetric_difference_update({i})
        elif cmd=='all': v['selected']=set(range(len(v['items'])))
        elif cmd=='none': v['selected']=set()
        else: raise WebError('عملیات نامعتبر')
        show_selection(chat)

def poll():
    offset=0
    while True:
        try:
            cleanup()
            for u in BALE.call('getUpdates',offset=offset,timeout=25) or []:
                offset=max(offset,int(u['update_id'])+1); q=u.get('callback_query'); m=q.get('message',{}) if q else u.get('message',{})
                if q:
                    try: BALE.call('answerCallbackQuery',callback_query_id=q['id'])
                    except RuntimeError: pass
                chat=m.get('chat',{}); sender=q.get('from',{}) if q else m.get('from',{})
                if chat.get('type')!='private' or str(chat.get('id'))!=str(sender.get('id')): continue
                cid=chat['id']; action=q.get('data','') if q else m.get('text','')
                try: dispatch(cid,action,None if q else m.get('message_id'),bool(q))
                except (WebError,ValueError) as e: BALE.say(cid,str(e))
                except Exception: BALE.say(cid,'خطای داخلی؛ با مدیر سامانه تماس بگیرید.')
        except Exception:
            print('Polling temporarily unavailable',flush=True); time.sleep(3)

if __name__=='__main__':
    if not TOKEN: raise SystemExit('BALE_BOT_TOKEN is missing in .env')
    init_db()
    from waitress import serve
    threading.Thread(target=poll,daemon=True).start()
    serve(app_factory(),host=os.getenv('LISTEN_HOST','127.0.0.1'),port=int(os.getenv('LISTEN_PORT','8796')),threads=4)
