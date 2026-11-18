import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩','01234567890123456789')

def norm(v):
    return '' if v is None else str(v).translate(DIGITS).strip()

def path(obj, key):
    if key == '': return obj
    if key is None: return None
    for part in key.split('.'):
        if not isinstance(obj, dict): return None
        obj = obj.get(part)
    return obj

def jalali(v):
    s = norm(v).replace('-', '/')
    if not re.fullmatch(r'\d{4}/\d{1,2}/\d{1,2}', s): return None
    y,m,d = map(int,s.split('/'))
    if not (1<=m<=12 and 1<=d<=31): return None
    return f'{y:04d}/{m:02d}/{d:02d}'

def today_jalali():
    import jdatetime
    d = jdatetime.date.fromgregorian(date=datetime.now(ZoneInfo('Asia/Tehran')).date())
    return d.year,d.month,d.day

def period(today):
    y,m,d = today
    sy,sm = shift_month(y,m,-1 if d<=20 else 0)
    ey,em = shift_month(y,m,0 if d<=20 else 1)
    return f'{sy:04d}/{sm:02d}/21',f'{ey:04d}/{em:02d}/20'

def shift_month(year,month,delta):
    if not (1<=month<=12): raise ValueError('ماه نامعتبر است')
    value=year*12+month-1+delta
    return divmod(value,12)[0],divmod(value,12)[1]+1

def period_options(today,count=12):
    if not isinstance(count,int) or not 1<=count<=24: raise ValueError('تعداد دوره نامعتبر است')
    start,_=period(today); year,month,_=map(int,start.split('/'))
    out=[]
    for offset in range(count):
        sy,sm=shift_month(year,month,-offset); ey,em=shift_month(sy,sm,1)
        out.append((f'{sy:04d}/{sm:02d}/21',f'{ey:04d}/{em:02d}/20'))
    return out

def minutes(v, duration=False):
    s=norm(v)
    if not re.fullmatch(r'\d{1,5}:\d{2}',s): return None
    h,m=map(int,s.split(':'))
    if m>=60 or (not duration and h>=24): return None
    return 60*h+m

def hhmm(n): return f'{n//60:02d}:{n%60:02d}'

def has_recorded_duration(value):
    """True for positive minutes, False for zero/blank, None for malformed."""
    s=norm(value)
    if not s: return False
    if re.fullmatch(r'\d+(?:\.0+)?',s): return int(float(s))>0
    m=minutes(s,True)
    return (m>0) if m is not None else None

def rows_at(obj, key):
    rows=path(obj,key)
    if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):
        raise ValueError('ساختار لیست API با تنظیمات تطابق ندارد')
    return rows

@dataclass(frozen=True)
class Proposal:
    date:str
    start:str
    end:str
    mins:int
    day:str
    kind:str='late'  # late | no_punches


def vacation_query(cfg, start, end, person, search_kind=None):
    return {**cfg.get('query_params', {}),
            **({'searchKind':search_kind} if search_kind is not None else {}),
            cfg['begin_date_param']:start, cfg['end_date_param']:end,
            cfg['person_param']:person}


def vacation_state(row,cfg):
    # searchKind is a filter, never a record status.
    code=norm(path(row,cfg.get('status_field')))
    if code in {norm(x) for x in cfg.get('pending_statuses',[])}: return 'pending'
    if code in {norm(x) for x in cfg.get('approved_statuses',[])}: return 'approved'
    return 'unknown'


def pending_requests(existing,pending,cfg):
    # The full-list status code independently detects a missed pending-filter row.
    return [r for r in (existing or []) if vacation_state(r,cfg)=='pending'] + [r for r in (pending or []) if vacation_state(r,cfg)!='approved']


def vacation_line(r,cfg):
    a=jalali(path(r,cfg.get('begin_date_field')))
    b=jalali(path(r,cfg.get('end_date_field')))
    if not a or not b or a>b: raise ValueError('تاریخ مرخصی نامعتبر است')
    date=a if a==b else f'{a} تا {b}'
    bt=norm(path(r,cfg.get('begin_time_field'))); et=norm(path(r,cfg.get('end_time_field')))
    when=f' | {bt} تا {et}' if bt and et else ''
    state=vacation_state(r,cfg)
    label={'pending':'مرخصی تایید نشده (درخواست جدید)','approved':'تایید نهایی'}.get(state) or norm(path(r,cfg.get('status_text_field'))) or 'وضعیت نامشخص'
    name=norm(path(r,cfg.get('name_field')))
    page=norm(path(r,cfg.get('page_field')))
    return f'{date}{when} | {name or "مرخصی"} | {label}'+(f' | شماره {page}' if page else '')


def coverage_dates(existing,cfg, respect_ignored=True):
    # Conservatively exclude every day covered by any non-ignored request.
    out=[]
    a,b=cfg.get('begin_date_field'),cfg.get('end_date_field')
    if not a or not b: raise ValueError('فیلد تاریخ مرخصی قبلی مشخص نیست')
    ignored={norm(x) for x in cfg.get('ignored_statuses',[])} if respect_ignored else set()
    status=cfg.get('status_field')
    for r in existing:
        if status and norm(path(r,status)) in ignored: continue
        start,end=jalali(path(r,a)),jalali(path(r,b))
        if not start or not end or start>end: raise ValueError('تاریخ درخواست قبلی نامعتبر است')
        out.append((start,end))
    return out


def weekday_of(date):
    """Gregorian weekday of a Jalali date: Mon=0 ... Thu=3 Fri=4 Sat=5 Sun=6.

    The field DayName from WebKart is not trusted for this decision.
    """
    import jdatetime
    y, m, d = map(int, date.split('/'))
    return jdatetime.date(y, m, d).togregorian().weekday()


def load_holidays(filename):
    import json
    from pathlib import Path
    raw=json.loads(Path(filename).read_text(encoding='utf-8'))
    if not isinstance(raw,dict) or not isinstance(raw.get('years'),dict) or not isinstance(raw.get('company_closed'),list):
        raise ValueError('ساختار فایل تعطیلات نامعتبر است')
    normalized={'years':{},'company_closed':set()}
    for year, dates in raw['years'].items():
        if not (isinstance(year,str) and re.fullmatch(r'\d{4}',year) and isinstance(dates,list)):
            raise ValueError('سال یا فهرست تعطیلات نامعتبر است')
        parsed={jalali(d) for d in dates}
        if None in parsed or any(d[:4]!=year for d in parsed):
            raise ValueError('تاریخ‌های تعطیلی نامعتبرند')
        normalized['years'][year]=parsed
    dates={jalali(d) for d in raw['company_closed']}
    if None in dates: raise ValueError('تاریخ تعطیلی شرکت نامعتبر است')
    normalized['company_closed']=dates
    return normalized


def day_off(date, day, cfg):
    """Return (is_off, reason, weekday) or (None, reason, weekday) if unknown."""
    dow=weekday_of(date)
    if dow in (3,4): return True,'تعطیلی آخر هفته (پنجشنبه / جمعه)',dow
    hc=cfg.get('holidays') or {}
    if date in hc.get('company_closed',set()): return True,'تعطیلی اعلام‌شده شرکت',dow
    years=hc.get('years',{})
    # Always require the annual official holiday dataset for non-weekend dates.
    if date[:4] not in years:
        return None,'تقویم تعطیلات رسمی این سال بارگذاری نشده؛ بررسی دستی',dow
    if date in years[date[:4]]: return True,'تعطیل رسمی',dow
    flag=norm(day.get('HOLIDAY'))
    if flag=='1': return True,'تعطیل طبق WebKart',dow
    if flag!='0': return None,'وضعیت تعطیلی WebKart نامشخص؛ بررسی دستی',dow
    return False,'',dow


def expected_end(cfg, dow):
    schedule=cfg['schedule']
    if dow in (5,6,0,1):
        end=minutes(schedule['saturday_to_tuesday_end'])
    elif dow==2:
        end=minutes(schedule['wednesday_end'])
    else:
        return None
    if end is None or end<=minutes(schedule['fixed_start']):
        raise ValueError('ساعت پایان برنامه کاری معتبر نیست')
    return end


def analyze(days, io_response, cfg, today, existing=None, pending=None, today_time=None, date_range=None):
    if date_range is None:
        begin,end=period(today)
    else:
        begin,end=(jalali(v) for v in date_range)
        if not begin or not end or begin>end: raise ValueError('بازه دوره نامعتبر است')
    now=f'{today[0]:04d}/{today[1]:02d}/{today[2]:02d}'
    io=cfg['io']
    missing=[k for k in ('rows_path','date_field','entry_time_field') if io.get(k) is None or (k!='rows_path' and not io.get(k))]
    if missing: raise ValueError('تنظیمات تردد ناقص: '+', '.join(missing))
    direction=io.get('direction_field')
    entry_values={norm(x) for x in io.get('entry_values',[])}
    exit_values={norm(x) for x in io.get('exit_values',[])}
    if direction:
        if not entry_values or not exit_values or entry_values & exit_values:
            raise ValueError('کدهای مجزای ورود و خروج باید مشخص شوند')
    elif not io.get('exit_time_field') or io.get('exit_time_field')==io['entry_time_field']:
        raise ValueError('فیلد مستقل خروج یا جهت تردد لازم است')
    expected=minutes(cfg['schedule']['fixed_start'])
    if expected is None: raise ValueError('ساعت مبنا نامعتبر است')
    clock=minutes(today_time) if today_time is not None else None
    if today_time is not None and clock is None:
        raise ValueError('ساعت فعلی نامعتبر است')
    rows=rows_at(io_response,io['rows_path']); indexed={}
    for row in rows:
        date=jalali(path(row,io['date_field']))
        if date is None: raise ValueError('فیلد تاریخ تردد نامعتبر است')
        indexed.setdefault(date,[]).append(row)
    covered=coverage_dates(existing,cfg['existing_requests']) if existing is not None else []
    pending_dates=coverage_dates(pending_requests(existing,pending,cfg['existing_requests']),cfg['existing_requests'],respect_ignored=False)
    proposals=[]; skipped=[]; seen=set()
    for day in days:
        date=jalali(day.get('DDATE'))
        if not date or not begin<=date<=min(end,now): continue
        if date in seen: raise ValueError('رکورد کارکرد تکراری')
        seen.add(date)
        off,reason,dow=day_off(date,day,cfg)
        if off is True: continue
        if off is None:
            skipped.append((date,reason)); continue
        shift_end=expected_end(cfg,dow)
        pending_today=any(a<=date<=b for a,b in pending_dates)
        if pending_today:
            skipped.append((date,'مرخصی تایید نشده (در حال پیگیری)؛ ثبت مجدد انجام نمی‌شود'))
        rr=indexed.get(date,[])
        entry_field=io['entry_time_field']; exit_field=io.get('exit_time_field') or entry_field
        if direction:
            kinds=[norm(path(r,direction)) for r in rr]
            if any(k not in entry_values|exit_values for k in kinds):
                skipped.append((date,'جهت تردد نامشخص؛ بررسی دستی')); continue
            raw_in=[path(r,entry_field) for r,k in zip(rr,kinds) if k in entry_values]
            raw_out=[path(r,exit_field) for r,k in zip(rr,kinds) if k in exit_values]
        else:
            raw_in=[path(r,entry_field) for r in rr]
            raw_out=[path(r,exit_field) for r in rr]
        entries=[minutes(x) for x in raw_in if norm(x)]
        exits=[minutes(x) for x in raw_out if norm(x)]
        if any(t is None for t in entries+exits):
            skipped.append((date,'ساعت تردد نامعتبر؛ بررسی دستی')); continue
        if entries and not exits:
            msg='عدم ثبت خروج، بررسی گردد'
            if date==now: msg+=' (امروز؛ ممکن است روز کاری هنوز تمام نشده باشد)'
            skipped.append((date,msg)); continue
        if exits and not entries:
            skipped.append((date,'عدم ثبت ورود، بررسی گردد')); continue
        if pending_today: continue
        if any(a<=date<=b for a,b in covered):
            skipped.append((date,'درخواست قبلی موجود است؛ ثبت مجدد انجام نمی‌شود')); continue
        # Any previously recorded leave / mission blocks another overlapping request.
        if any(has_recorded_duration(day.get(k)) is not False for k in ('Morakhasi','MISSION')):
            skipped.append((date,'مرخصی یا مأموریت در کارکرد ثبت شده؛ بررسی دستی')); continue
        if not entries and not exits:
            # No punches: user specifically requests one hourly vacation covering
            # the entire shift, NOT an automatic submission.
            if date==now and (clock is None or clock<shift_end):
                skipped.append((date,'امروز هنوز پایان ساعت موظفی تأیید نشده؛ بررسی در پایان روز')); continue
            if any(has_recorded_duration(day.get(k)) is not False for k in ('LEAVEOFABSPAY','LEAVEOFABSNOPAY')):
                skipped.append((date,'مرخصی با/بدون حقوق در کارکرد ثبت شده؛ بررسی دستی')); continue
            worked=0 if norm(day.get('WORKTOTAL')) in ('0','0.0') else minutes(day.get('WORKTOTAL'),True)
            if worked is None:
                skipped.append((date,'کارکرد روزانه نامشخص؛ نمی‌توان نبود تردد را غیبت قطعی دانست')); continue
            if worked>0:
                skipped.append((date,'تردد خام وجود ندارد اما کارکرد مثبت است؛ بررسی دستی')); continue
            proposals.append(Proposal(date,hhmm(expected),hhmm(shift_end),shift_end-expected,norm(day.get('DayName')),'no_punches'))
            continue
        actual=min(entries)
        if max(exits)<actual:
            skipped.append((date,'ترتیب ورود و خروج نامعتبر؛ بررسی دستی')); continue
        delay=actual-expected
        if delay<int(cfg.get('min_delay_minutes',1)): continue
        # For the recorded punch case use first entry, not total shortage.
        if norm(day.get('DAILYABSENCE'))!='0':
            skipped.append((date,'غیبت ثبت‌شده همراه با تردد؛ بررسی دستی')); continue
        proposals.append(Proposal(date,hhmm(expected),hhmm(actual),delay,norm(day.get('DayName')),'late'))
    return sorted(proposals,key=lambda x:x.date),skipped
