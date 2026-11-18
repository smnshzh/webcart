import unittest
from datetime import date as GDate, timedelta
from unittest.mock import patch
from engine import period,period_options,analyze,vacation_query,vacation_state,vacation_line,load_holidays,day_off
from copy import deepcopy

CFG={'io':{'rows_path':'Values.rows','date_field':'date','entry_time_field':'time','direction_field':'direction','entry_values':['IN'],'exit_values':['OUT']},'schedule':{'fixed_start':'08:15','saturday_to_tuesday_end':'17:30','wednesday_end':'16:30'},'holidays':{'years':{'1405':set()},'company_closed':set()},'existing_requests':{'begin_date_field':'a','end_date_field':'b'},'min_delay_minutes':1}
DAY={'DDATE':'۱۴۰۵/۰۸/۰۳','DayName':'شنبه','HOLIDAY':0,'DAILYABSENCE':0,'Morakhasi':'۰۰:۰۰','MISSION':'۰۰:۰۰','totalKasrKar':'۰۰:۲۵','WORKTOTAL':'08:00'}
IO={'Values':{'rows':[{'date':'1405/08/03','time':'08:39','direction':'IN'},{'date':'1405/08/03','time':'17:30','direction':'OUT'}]}}
class Tests(unittest.TestCase):
    @staticmethod
    def calendar_weekday(date):
        # Local offline test adapter: 1405/01/01 was Sat 2026-03-21.
        y,m,d=map(int,date.split('/'))
        if y!=1405: raise ValueError('Only 1405 is used for weekday tests')
        offset=(m-1)*31 if m<=6 else 6*31+(m-7)*30
        return (GDate(2026,3,21)+timedelta(days=offset+d-1)).weekday()
    def setUp(self):
        self.weekday_patch=patch('engine.weekday_of',side_effect=self.calendar_weekday)
        self.weekday_patch.start()
    def tearDown(self):
        self.weekday_patch.stop()
    def run_case(self,day=DAY,io=IO,cfg=CFG,today=(1405,8,19),existing=None,pending=None,today_time=None):
        return analyze([day],io,cfg,today,[] if existing is None else existing,pending,today_time=today_time)
    def test_cycle(self):
        for d,a,b in [((1405,8,19),'1405/07/21','1405/08/20'),((1405,8,20),'1405/07/21','1405/08/20'),((1405,8,21),'1405/08/21','1405/09/20'),((1405,9,2),'1405/08/21','1405/09/20'),((1406,1,5),'1405/12/21','1406/01/20'),((1405,12,21),'1405/12/21','1406/01/20')]: self.assertEqual(period(d),(a,b))
    def test_period_options_cross_year(self):
        self.assertEqual(period_options((1406,1,5),3),[
            ('1405/12/21','1406/01/20'),
            ('1405/11/21','1405/12/20'),
            ('1405/10/21','1405/11/20'),
        ])
    def test_analyze_uses_selected_period(self):
        day=dict(DAY,DDATE='1405/07/25')
        io={'Values':{'rows':[{'date':'1405/07/25','time':'08:39','direction':'IN'},{'date':'1405/07/25','time':'17:30','direction':'OUT'}]}}
        proposals,_=analyze([day],io,CFG,(1405,8,25),[],None,date_range=('1405/07/21','1405/08/20'))
        self.assertEqual([p.date for p in proposals],['1405/07/25'])
    def test_vacation_query(self):
        from pathlib import Path
        import json
        cfg=json.loads(Path(__file__).with_name('config.json').read_text())['existing_requests']
        self.assertEqual(cfg['endpoint'],'Vacation/PersonVacationList')
        self.assertEqual(vacation_query(cfg,'1405/07/21','1405/08/20','test'),{'searchKind':5,'sortType':'Desc','sortExpression':'beginDate','beginDate':'1405/07/21','endDate':'1405/08/20','perno':'test'})
    def test_status_ignored_when_explicitly_mapped(self):
        cfg=deepcopy(CFG);cfg['existing_requests'].update(status_field='status',ignored_statuses=[3,4])
        for status in [1,2,3,4,99]:
            p,_=self.run_case(cfg=cfg,existing=[{'a':'1405/08/03','b':'1405/08/03','status':status}])
            self.assertEqual(bool(p),status in [3,4])
    def test_pending_blocks_and_reports_even_if_all_list_is_empty(self):
        p,s=self.run_case(pending=[{'a':'1405/08/03','b':'1405/08/03'}])
        self.assertFalse(p);self.assertIn('مرخصی تایید نشده',s[0][1])
    def test_pending_with_recorded_leave(self):
        p,s=self.run_case(day=dict(DAY,Morakhasi='00:24'),pending=[{'a':'1405/08/03','b':'1405/08/03'}])
        self.assertFalse(p);self.assertIn('مرخصی تایید نشده',s[0][1])
    def test_pending_and_missing_exit_both_reported(self):
        p,s=self.run_case(io={'Values':{'rows':IO['Values']['rows'][:1]}},pending=[{'a':'1405/08/03','b':'1405/08/03'}])
        self.assertFalse(p);self.assertEqual(len(s),2);self.assertIn('عدم ثبت خروج',s[1][1])
    def test_pending_multiday(self):
        p,s=self.run_case(pending=[{'a':'1405/08/02','b':'1405/08/04'}])
        self.assertFalse(p);self.assertIn('مرخصی تایید نشده',s[0][1])
    def test_pending_other_date_not_blocking(self):
        p,s=self.run_case(pending=[{'a':'1405/08/04','b':'1405/08/04'}])
        self.assertTrue(p);self.assertFalse(s)
    def test_pending_filter_overrides_all(self):
        c={'begin_date_param':'beginDate','end_date_param':'endDate','person_param':'perno','query_params':{'searchKind':5,'sortType':'Desc'}}
        self.assertEqual(vacation_query(c,'a','b','p',1)['searchKind'],1)
        self.assertEqual(vacation_query(c,'a','b','p')['searchKind'],5)
    def test_pending_on_time_still_reports_status(self):
        io=deepcopy(IO);io['Values']['rows'][0]['time']='08:15'
        p,s=self.run_case(io=io,pending=[{'a':'1405/08/03','b':'1405/08/03'}]);self.assertFalse(p);self.assertIn('مرخصی تایید نشده',s[0][1])
    def test_late(self):
        p,s=self.run_case(); self.assertEqual((p[0].start,p[0].end,p[0].mins),('08:15','08:39',24)); self.assertFalse(s)
    def test_future(self): self.assertFalse(self.run_case(today=(1405,8,2))[0])
    def test_existing(self): self.assertFalse(self.run_case(existing=[{'a':'1405/08/03','b':'1405/08/03'}])[0])
    def test_holiday(self): self.assertFalse(self.run_case(day=dict(DAY,HOLIDAY=1))[0])
    def test_existing_leave(self): self.assertFalse(self.run_case(day=dict(DAY,Morakhasi='00:10'))[0])
    def test_shortage_not_used_as_delay(self): self.assertEqual(self.run_case(day=dict(DAY,totalKasrKar='00:05'))[0][0].mins,24)
    def test_no_input(self): self.assertIn('کارکرد مثبت',self.run_case(io={'Values':{'rows':[]}})[1][0][1])
    def test_no_exit_with_shortage(self):
        p,s=self.run_case(io={'Values':{'rows':IO['Values']['rows'][:1]}}); self.assertFalse(p); self.assertEqual(s[0][1],'عدم ثبت خروج، بررسی گردد')
    def test_today_no_exit(self):
        p,s=self.run_case(io={'Values':{'rows':IO['Values']['rows'][:1]}},today=(1405,8,3)); self.assertFalse(p);self.assertIn('امروز',s[0][1])
    def test_on_time(self):
        io=deepcopy(IO); io['Values']['rows'][0]['time']='۰۸:۱۵';self.assertFalse(self.run_case(io=io)[0])
    def test_zero_shortage_late(self): self.assertEqual(self.run_case(day=dict(DAY,totalKasrKar='00:00'))[0][0].mins,24)
    def test_paired(self):
        cfg=deepcopy(CFG); cfg['io'].update(direction_field=None,entry_time_field='in',exit_time_field='out')
        io={'Values':{'rows':[{'date':'1405/08/03','in':'08:39','out':'17:30'}]}}
        self.assertEqual(self.run_case(io=io,cfg=cfg)[0][0].mins,24)
        io['Values']['rows'][0]['out']=''; self.assertIn('عدم ثبت خروج',self.run_case(io=io,cfg=cfg)[1][0][1])
    def test_unknown_exit(self):
        cfg=deepcopy(CFG);cfg['io']['exit_values']=[]
        with self.assertRaises(ValueError):self.run_case(cfg=cfg)
    def full_absence(self,date='1405/08/03',**changes):
        day=dict(DAY,DDATE=date,WORKTOTAL='00:00',totalKasrKar='08:00',DAILYABSENCE=1,**changes)
        return day
    def test_full_absence_sunday(self):
        p,s=self.run_case(day=self.full_absence(),io={'Values':{'rows':[]}})
        self.assertFalse(s)
        self.assertEqual([(q.start,q.end,q.mins,q.kind) for q in p],[('08:15','17:30',555,'no_punches')])
    def test_full_absence_saturday_to_tuesday(self):
        for d in ['02','03','04','05']:
            with self.subTest(d=d):
                p,_=self.run_case(day=self.full_absence(date=f'1405/08/{d}'),io={'Values':{'rows':[]}})
                self.assertEqual((p[0].start,p[0].end,p[0].mins),('08:15','17:30',555))
    def test_full_absence_wednesday(self):
        p,s=self.run_case(day=self.full_absence(date='1405/08/06'),io={'Values':{'rows':[]}})
        self.assertFalse(s); self.assertEqual((p[0].start,p[0].end,p[0].mins),('08:15','16:30',495))
    def test_thursday_friday_always_off(self):
        for date in ['1405/08/07','1405/08/08']:
            with self.subTest(date=date):
                p,s=self.run_case(day=self.full_absence(date=date),io={'Values':{'rows':[]}})
                self.assertFalse(p); self.assertFalse(s)
    def test_official_holiday_without_webkart_flag(self):
        cfg=deepcopy(CFG)
        from pathlib import Path
        cfg['holidays']=load_holidays(Path(__file__).with_name('holidays.json'))
        d=self.full_absence(date='1405/10/02')
        p,s=self.run_case(day=d,io={'Values':{'rows':[]}},cfg=cfg,today=(1405,10,3))
        self.assertFalse(p);self.assertFalse(s)
    def test_company_closure(self):
        cfg=deepcopy(CFG); cfg['holidays']['company_closed']={'1405/08/03'}
        p,s=self.run_case(day=self.full_absence(),io={'Values':{'rows':[]}},cfg=cfg)
        self.assertFalse(p);self.assertFalse(s)
    def test_missing_annual_holiday_calendar_fails_closed(self):
        cfg=deepcopy(CFG);cfg['holidays']={'years':{},'company_closed':set()}
        p,s=self.run_case(day=self.full_absence(),io={'Values':{'rows':[]}},cfg=cfg)
        self.assertFalse(p);self.assertIn('تقویم تعطیلات',s[0][1])
    def test_unknown_webkart_holiday_flag_fails_closed(self):
        day=self.full_absence();day.pop('HOLIDAY')
        p,s=self.run_case(day=day,io={'Values':{'rows':[]}})
        self.assertFalse(p);self.assertIn('وضعیت تعطیلی',s[0][1])
    def test_full_absence_pending_blocks(self):
        p,s=self.run_case(day=self.full_absence(),io={'Values':{'rows':[]}},pending=[{'a':'1405/08/03','b':'1405/08/03'}])
        self.assertFalse(p);self.assertIn('مرخصی تایید نشده',s[0][1])
    def test_full_absence_approved_blocks(self):
        p,s=self.run_case(day=self.full_absence(),io={'Values':{'rows':[]}},existing=[{'a':'1405/08/03','b':'1405/08/03'}])
        self.assertFalse(p);self.assertIn('درخواست قبلی',s[0][1])
    def test_full_absence_with_registered_leave_blocks(self):
        day=self.full_absence();day['Morakhasi']='08:15'
        p,s=self.run_case(day=day,io={'Values':{'rows':[]}})
        self.assertFalse(p);self.assertIn('مرخصی یا مأموریت',s[0][1])
    def test_full_absence_with_paid_leave_blocks(self):
        day=self.full_absence();day['LEAVEOFABSPAY']='450'
        p,s=self.run_case(day=day,io={'Values':{'rows':[]}})
        self.assertFalse(p);self.assertIn('مرخصی با/بدون حقوق',s[0][1])
    def test_full_absence_zero_shortage_still_proposes(self):
        day=self.full_absence();day['totalKasrKar']='00:00'
        p,s=self.run_case(day=day,io={'Values':{'rows':[]}})
        self.assertEqual(len(p),1);self.assertFalse(s)
    def test_full_absence_today_before_finish_is_skipped(self):
        p,s=self.run_case(day=self.full_absence(),io={'Values':{'rows':[]}},today=(1405,8,3))
        self.assertFalse(p);self.assertIn('پایان ساعت موظفی',s[0][1])
        p,s=self.run_case(day=self.full_absence(),io={'Values':{'rows':[]}},today=(1405,8,3),today_time='18:00')
        self.assertEqual(len(p),1);self.assertFalse(s)
    def test_full_absence_with_positive_work_blocks(self):
        p,s=self.run_case(io={'Values':{'rows':[]}})
        self.assertFalse(p);self.assertIn('کارکرد مثبت',s[0][1])
    def test_actual_holidays_dataset(self):
        from pathlib import Path
        h=load_holidays(Path(__file__).with_name('holidays.json'))
        self.assertEqual(len(h['years']['1405']),26)
        self.assertIn('1405/08/22',h['years']['1405'])
        self.assertIn('1405/10/02',h['years']['1405'])
    def actual_cfg(self):
        from pathlib import Path
        import json
        cfg=deepcopy(CFG)
        cfg['existing_requests']=json.loads(Path(__file__).with_name('config.json').read_text())['existing_requests']
        return cfg
    def actual_row(self,status=0,**kw):
        return dict({'BeginDate':'۱۴۰۵/۰۸/۰۳','EndDate':'۱۴۰۵/۰۸/۰۳','BeginTime':'۰۸:۱۵','EndTime':'۰۸:۳۹','Status':status,'Type':2.0,'IsDaily':0,'Name':'استحقاقی ساعتی','Pageno':'۱۲۳   '},**kw)
    def test_actual_pending_from_all_only(self):
        p,s=self.run_case(cfg=self.actual_cfg(),existing=[self.actual_row()],pending=[])
        self.assertFalse(p); self.assertIn('مرخصی تایید نشده',s[0][1])
    def test_actual_approved(self):
        p,s=self.run_case(cfg=self.actual_cfg(),existing=[self.actual_row(10)])
        self.assertFalse(p); self.assertNotIn('مرخصی تایید نشده',s[0][1])
    def test_actual_unknown_blocks(self):
        p,s=self.run_case(cfg=self.actual_cfg(),existing=[self.actual_row(3)])
        self.assertFalse(p); self.assertNotIn('مرخصی تایید نشده',s[0][1])
    def test_actual_daily_range(self):
        p,s=self.run_case(cfg=self.actual_cfg(),existing=[self.actual_row(0,BeginDate='۱۴۰۵/۰۸/۰۲',EndDate='۱۴۰۵/۰۸/۰۴',IsDaily=1,Type=1,BeginTime=' ',EndTime=' ')])
        self.assertFalse(p); self.assertIn('مرخصی تایید نشده',s[0][1])
    def test_actual_display(self):
        c=self.actual_cfg()['existing_requests']; r=self.actual_row()
        self.assertEqual(vacation_state(r,c),'pending')
        self.assertIn('شماره 123',vacation_line(r,c)); self.assertIn('08:15 تا 08:39',vacation_line(r,c))
        self.assertIn('تایید نهایی',vacation_line(self.actual_row(10),c))
        self.assertIn('پذیرفته نشده',vacation_line(self.actual_row(3,StatusType='پذیرفته نشده'),c))
    def test_actual_merged_io_mapping(self):
        from pathlib import Path
        import json
        cfg=deepcopy(CFG)
        cfg['io']=json.loads(Path(__file__).with_name('config.json').read_text())['io']
        merged={'Values':{'Table':[
            {'beginDate':'۱۴۰۵/۰۸/۰۳','PERNO':' 1144069','BeginTime':'۰۸:۳۹','endTime':'','type':'ورود'},
            {'beginDate':'۱۴۰۵/۰۸/۰۳','PERNO':' 1144069','BeginTime':'','endTime':'۱۷:۳۰','type':'خروج'},
        ]}}
        proposals,skipped=analyze([DAY],merged,cfg,(1405,8,19),[],None)
        self.assertEqual([(p.start,p.end,p.mins) for p in proposals],[('08:15','08:39',24)])
        self.assertFalse(skipped)
if __name__=='__main__': unittest.main()
