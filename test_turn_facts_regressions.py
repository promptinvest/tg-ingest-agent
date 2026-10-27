"""Actual rejected qualification replies keep current-turn facts during correction."""
import copy
from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import converse
import converse_svc
import llm
import store
import tg_ingest_agent as entry
from test_context_continuity import prepare_context_case
from testlib import make_config
from texts import T

RU_FIRST = "Босс, я тут! Сейчас всё в порядке, вижу тебя отлично 👌\n\nСлушаю — что там у тебя?"
RU_RETRY = "Ага, теперь вижу! 👋\n\nТы проверял, доходят ли сообщения? Всё работает, я тут. Что хотел, босс?"
RU_FORCED_RETRY = "Вижу тебя, босс! Сейчас всё отлично — твоё сообщение дошло. Что хотел?"
EN_RECOVERY = ("Here, boss — and yes, I can read you loud and clear this time. "
               "Whatever was glitching earlier seems to have smoothed itself out. What's up?")
BAD_DATE = ("1. Статус: задача «Проверка бюджета» запланирована, ещё не начата.\n"
            "2. Следующий шаг: проверить расписание; встреча по плану — 28 октября 2026 "
            "(это было вчера, босс, возможно, дату пора обновить).")


class TurnFactsRegressions(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.agent=entry.Agent(make_config(ALLOWED_CHAT_IDS="1",DB_PATH=":memory:",
            MEDIA_DIR=str(Path(temp.name)/"media")))
        self.addCleanup(self.agent.conn.close);self.conn=self.agent.conn;self.seen=[]
    def owner(self,text):
        store.convo_add(self.conn,1,"user","Earlier request",source="boss")
        store.convo_add(self.conn,1,"bot",T("ru","update_dead_letter"))
        store.convo_add(self.conn,1,"user",text,source="boss")
        return text
    def run_reply(self,text,outputs,lang="ru"):
        def chat(cfg,conn,skill,messages,**kwargs):
            self.seen.append(copy.deepcopy(messages))
            self.assertLessEqual(len(self.seen),2,"one bounded correction only")
            return outputs(messages,len(self.seen)) if callable(outputs) else outputs[len(self.seen)-1]
        fixed=datetime(2026,10,27,16,33,tzinfo=timezone.utc)
        with mock.patch.object(llm,"chat_profile",side_effect=chat), \
             mock.patch.object(llm,"embed",return_value=[[1.0,0.0]]), \
             mock.patch.object(self.agent,"send_chat_action"), \
             mock.patch.object(self.agent,"maybe_curate_conversation"), \
             mock.patch.object(self.agent,"reply",return_value=True) as reply, \
             mock.patch.object(converse_svc,"datetime",wraps=datetime) as clock:
            clock.now.return_value=fixed
            self.agent.do_converse(1,lang,text)
        return reply.call_args.args[1]
    def test_actual_english_implied_recovery_is_repaired_instead_of_shipped(self):
        text=self.owner("And now?")
        good="I can read this message. What would you like me to check?"
        self.assertEqual(self.run_reply(text,[EN_RECOVERY,good],"en"),good)
        self.assertEqual(len(self.seen),2)
    def test_actual_relative_date_error_is_repaired_against_frozen_current_date(self):
        case=prepare_context_case(self.agent,"ru_source_data")
        good=("1. Статус: задача запланирована, пока не начата.\n"
              "2. Следующий шаг: проверить расписание; дата встречи в источнике — 28 октября 2026.")
        self.assertEqual(self.run_reply(case["text"],[BAD_DATE,good]),good)
        self.assertEqual(len(self.seen),2)
    def test_actual_russian_first_and_retry_failures_need_near_current_positive_facts(self):
        text=self.owner("А сейчас?");good="Твоё сообщение вижу. Что хочешь проверить?"
        def output(messages,call):
            if call==1:return RU_FIRST
            nearby="\n".join(m["content"] for m in messages[-4:] if m["role"]=="system")
            return good if "CURRENT TURN FACTS" in nearby and "Local date:2026-10-27" in nearby else RU_RETRY
        self.assertEqual(self.run_reply(text,output),good)
        self.assertEqual(self.seen[1][1:len(self.seen[0])],self.seen[0][1:])
    def test_actual_forced_recovery_retry_keeps_current_facts_beside_same_request(self):
        text=self.owner("А сейчас?");good="Это сообщение читаю. Что именно проверить?"
        def output(messages,call):
            if call==1:return T("ru","update_dead_letter")
            nearby="\n".join(m["content"] for m in messages[-4:] if m["role"]=="system")
            return good if "CURRENT TURN FACTS" in nearby and "received and readable" in nearby else RU_FORCED_RETRY
        self.assertEqual(self.run_reply(text,output),good)
        self.assertEqual(len(self.seen),2)


if __name__=="__main__":unittest.main()
