import unittest
import time
from unittest.mock import patch

import bot


class FakeBale:
    def __init__(self):
        self.calls=[]
        self.messages=[]

    def call(self,method,**data):
        self.calls.append((method,data))
        return True

    def say(self,chat,text,buttons=None):
        self.messages.append((chat,text,buttons))
        return True


class FakeWebKart:
    def __init__(self):
        self.person=None
        self.closed=False
        self.calls=[]

    def login(self,person,password):
        assert person=='1144069'
        assert password=='synthetic-password'
        self.person=person
        return 'کاربر آزمایشی'

    def close(self):
        self.closed=True

    def call(self,method,endpoint,**kwargs):
        self.calls.append((method,endpoint,kwargs))
        return {'Values':{'TotallPersonDayWork':{}}}


class ChatLoginTests(unittest.TestCase):
    def setUp(self):
        self.original_bale=bot.BALE
        bot.BALE=FakeBale()
        bot.CHAT_LOGINS.clear()
        bot.PAIRS.clear()
        bot.PROPOSALS.clear()
        bot.SELECTED_PERIODS.clear()
        bot.USERS.clear()

    def tearDown(self):
        for client,_ in bot.USERS.values():
            client.close()
        bot.CHAT_LOGINS.clear()
        bot.PAIRS.clear()
        bot.PROPOSALS.clear()
        bot.SELECTED_PERIODS.clear()
        bot.USERS.clear()
        bot.BALE=self.original_bale

    def test_private_chat_login_deletes_password_message_and_activates_session(self):
        bot.dispatch(10,'login',None,True)
        self.assertEqual(bot.CHAT_LOGINS[10]['stage'],'username')

        bot.dispatch(10,'1144069',101,False)
        self.assertEqual(bot.CHAT_LOGINS[10]['stage'],'password')
        self.assertEqual(bot.CHAT_LOGINS[10]['username'],'1144069')

        with patch.object(bot,'WebKart',FakeWebKart), patch.object(bot,'scan'):
            bot.dispatch(10,'synthetic-password',102,False)

        self.assertNotIn(10,bot.CHAT_LOGINS)
        self.assertIn(10,bot.USERS)
        self.assertIn(('deleteMessage',{'chat_id':10,'message_id':102}),bot.BALE.calls)
        self.assertFalse(any('synthetic-password' in text for _,text,_ in bot.BALE.messages))

    def test_callback_is_not_treated_as_password_and_cancel_clears_state(self):
        bot.dispatch(20,'login',None,True)
        bot.dispatch(20,'1144069',201,False)
        bot.dispatch(20,'scan',None,True)
        self.assertEqual(bot.CHAT_LOGINS[20]['stage'],'password')
        self.assertFalse(any(method=='deleteMessage' for method,_ in bot.BALE.calls))

        bot.dispatch(20,'login:cancel',None,True)
        self.assertNotIn(20,bot.CHAT_LOGINS)

    def test_invalid_personnel_code_does_not_advance(self):
        bot.dispatch(30,'login',None,True)
        bot.dispatch(30,'abc',301,False)
        self.assertEqual(bot.CHAT_LOGINS[30]['stage'],'username')

    def test_selected_period_is_used_by_summary_and_clears_old_proposals(self):
        client=FakeWebKart(); client.person='1144069'
        bot.USERS[40]=(client,time.monotonic()+60)
        chosen=bot.period_options(bot.today_jalali())[1]
        bot.PROPOSALS[40]={'old':True,'expiry':time.monotonic()+60}

        bot.dispatch(40,'prd:'+bot.period_id(chosen),None,True)
        self.assertEqual(bot.SELECTED_PERIODS[40],chosen)
        self.assertNotIn(40,bot.PROPOSALS)

        bot.dispatch(40,'summary',None,True)
        _,endpoint,kwargs=client.calls[-1]
        self.assertEqual(endpoint,'GetPersonDayWorksTotal')
        self.assertEqual(kwargs['params']['begindate'],chosen[0])
        self.assertEqual(kwargs['params']['enddate'],chosen[1])

    def test_snapshot_uses_merged_period_io_and_validates_owner(self):
        client=bot.WebKart(); client.person='1144069'; calls=[]
        def fake_call(method,endpoint,**kwargs):
            calls.append((endpoint,kwargs))
            if endpoint=='GetPersonDayWorksTotal':
                return {'Values':{'WebPersonDayWorks':{'WebWorkDayList':[]}}}
            if endpoint=='ioInfo/GetPersonMergeIo':
                return {'Values':{'Table':[{'PERNO':'   1144069','beginDate':'۱۴۰۵/۰۸/۲۱','BeginTime':'۰۸:۱۵','endTime':''}]}}
            if endpoint=='Vacation/PersonVacationList':
                return {'Values':[],'TotalRow':0}
            self.fail('Unexpected endpoint: '+endpoint)
        with patch.object(client,'call',side_effect=fake_call):
            client.snapshot('1405/08/21','1405/09/20')
        client.close()
        self.assertEqual(calls[1][0],'ioInfo/GetPersonMergeIo')
        self.assertEqual(calls[1][1]['params'],{'beginDate':'1405/08/21','endDate':'1405/09/20','perno':'1144069'})

    def test_server_error_shows_safe_section_and_message(self):
        client=bot.WebKart()
        class Response:
            status_code=200
            def json(self):
                return {'ResponseResultType':24,'Message':'دوره انتخابی در دسترس نیست','RealMessage':'internal-secret'}
        with patch.object(client.http,'request',return_value=Response()):
            with self.assertRaises(bot.WebError) as caught:
                client.call('GET','ioInfo/GetPersonMergeIo',label='تردد دوره')
        client.close()
        text=str(caught.exception)
        self.assertIn('تردد دوره',text)
        self.assertIn('کد 24',text)
        self.assertIn('دوره انتخابی در دسترس نیست',text)
        self.assertNotIn('internal-secret',text)


if __name__=='__main__':
    unittest.main()
