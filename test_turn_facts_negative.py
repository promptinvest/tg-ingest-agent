"""Independent compatibility edges for transient current-turn facts.

Pure guards and frozen prompt values only: no database, model or delivery effects.
The dated statements below are quotations, narratives or accurate comparisons;
they must not be confused with a false claim about the current local calendar.
"""
from datetime import datetime, timedelta, timezone
import unittest

import converse


class TurnFactsCompatibilityTests(unittest.TestCase):
    def context(self, *, with_facts=True):
        current = "Current request"
        messages = [{"role": "system", "content": "Base conversation contract"}]
        if with_facts:
            messages.append({"role": "system", "content": converse.turn_facts_contract(
                datetime(2026, 10, 27, 19, 33, tzinfo=timezone(timedelta(hours=3))))})
        messages.append({"role": "user", "content": current})
        return converse.freeze_turn_context(1, "en", current, messages), messages

    def test_frozen_facts_keep_the_original_local_day_and_time(self):
        context, messages = self.context()
        facts = converse.frozen_turn_facts(context)
        self.assertTrue(facts.startswith("CURRENT TURN FACTS — code-owned"))
        self.assertIn("Local date:2026-10-27", facts)
        self.assertIn("19:33+03:00", facts)
        messages[1]["content"] = "Local date:2026-10-29; stale replacement"
        self.assertEqual(converse.frozen_turn_facts(context), facts)

    def test_correct_relative_date_relations_remain_valid(self):
        context, _ = self.context()
        replies = (
            "October 28, 2026 is tomorrow.",
            "2026-10-27 is today.",
            "October 26, 2026 was yesterday.",
            "28 октября 2026 — это завтра.",
            "27 октября 2026 — это сегодня.",
            "26 октября 2026 — это было вчера.",
        )
        for reply in replies:
            with self.subTest(reply=reply):
                self.assertIsNone(converse.relative_date_claim(reply, context))

    def test_quoted_translated_historical_and_conditional_dates_are_data(self):
        context, _ = self.context()
        cases = (
            ('"October 28, 2026 was yesterday."',
             'Quote exactly: "October 28, 2026 was yesterday."'),
            ("October 28, 2026 was yesterday.", "Repeat exactly: October 28, 2026 was yesterday."),
            ("October 28, 2026 was yesterday.", "Quote exactly: October 28, 2026 was yesterday."),
            ("In the novel, October 28, 2026 was yesterday.", "Summarize the novel timeline."),
            ("28 октября 2026 было вчера.",
             "Translate into Russian: October 28, 2026 was yesterday."),
            ("Yesterday you said, ‘October 28, 2026 was yesterday.’", "What did I say?"),
            ("The diary entry says: October 28, 2026 was yesterday.",
             "What does that diary entry say?"),
            ("In the novel, the narrator says October 28, 2026 was yesterday.",
             "Summarize the narrator's dated claim."),
            ("If today were October 29, 2026, October 28, 2026 would be yesterday.",
             "Explain a hypothetical calendar date."),
        )
        for reply, request in cases:
            with self.subTest(reply=reply):
                self.assertIsNone(converse.relative_date_claim(reply, context, request))

    def test_date_mentioned_in_a_past_discussion_is_not_the_discussion_day(self):
        context, _ = self.context()
        for reply in (
                "We discussed October 28 yesterday.",
                "We discussed October 28, 2026 yesterday.",
                "Вчера мы обсудили встречу 28 октября 2026.",
                "28 октября 2026 обсуждали вчера, а встреча ещё впереди."):
            with self.subTest(reply=reply):
                self.assertIsNone(converse.relative_date_claim(reply, context))

    def test_missing_turn_facts_do_not_invent_a_calendar_reference(self):
        context, _ = self.context(with_facts=False)
        self.assertEqual(converse.frozen_turn_facts(context), "")
        self.assertIsNone(converse.relative_date_claim(
            "October 28, 2026 was yesterday.", context))

    def test_mood_and_ordinary_advice_are_not_operational_recovery(self):
        cases = (
            ("Glad things have smoothed themselves out for you.", "I feel better after lunch."),
            ("Smooth out the rough paint once it is dry.", "How do I repair uneven paint?"),
            ("The conversation with your friend seems to have smoothed itself out.",
             "My friend and I made up."),
            ("Рада, что у тебя всё наладилось.", "Я помирился с другом."),
        )
        for reply, request in cases:
            with self.subTest(reply=reply):
                self.assertIsNone(converse.processing_status_claim(reply, request))

    def test_negated_uncertain_and_attributed_recovery_remain_reportable(self):
        cases = (
            ("I cannot confirm that the earlier glitch has resolved.", "And now?"),
            ("There is no evidence that the glitch has resolved.", "And now?"),
            ("I can't tell whether the earlier glitch has resolved.", "And now?"),
            ("That doesn't mean the earlier issue has been resolved.", "And now?"),
            ("It is unclear whether the earlier problem has resolved.", "And now?"),
            ("I'm not sure whether the earlier problem has resolved.", "And now?"),
            ("You reported that the earlier glitch had resolved.", "What did I report?"),
            ('You said, "Whatever was glitching earlier seems to have smoothed itself out."',
             "And now?"),
            ("Не могу подтвердить, что прежний сбой устранён.", "А сейчас?"),
        )
        for reply, request in cases:
            with self.subTest(reply=reply):
                self.assertIsNone(converse.processing_status_claim(reply, request))


if __name__ == "__main__":
    unittest.main()
