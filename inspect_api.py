"""Private console diagnostic: shows key names only, not tokens or HR values."""
from getpass import getpass
from bot import WebKart, WebError
from engine import period,today_jalali,norm

def schema(obj, prefix='', depth=0):
    if depth>6: return
    if isinstance(obj,dict):
        for k,v in obj.items():
            p=f'{prefix}.{k}' if prefix else k
            if any(s in k.lower() for s in ('password','token','secret')): continue
            print(p,':',type(v).__name__)
            if isinstance(v,(dict,list)): schema(v,p,depth+1)
    elif isinstance(obj,list) and obj: schema(obj[0],prefix+'[]',depth+1)

if __name__=='__main__':
    c=WebKart()
    try:
        c.login(norm(input('Personnel code: ')),getpass('Password: '))
        a,b=period(today_jalali())
        checks=[
            ('Daily-work response',lambda:c.call('GET','GetPersonDayWorksTotal',params={'begindate':a,'enddate':b,'perno':c.person})),
            ('Person IO response',lambda:c.call('GET','ioInfo/GetPersonIoInfo',label='تردد روزانه',params={'beginDate':a,'endDate':b,'perno':c.person})),
            ('Merged IO response',lambda:c.call('GET','ioInfo/GetPersonMergeIo',label='تردد دوره‌ای',params={'beginDate':a,'endDate':b,'perno':c.person})),
            ('Vacation types response',lambda:c.call('GET','Vacation/GetWebVacationTypeList')),
            ('Existing vacation requests response',lambda:c.vacation_list(a,b)),
        ]
        for label,fetch in checks:
            print(f'\n{label} schema:')
            try:
                schema(fetch())
            except WebError as e:
                print(f'Unavailable: {e}')
    finally: c.close()
