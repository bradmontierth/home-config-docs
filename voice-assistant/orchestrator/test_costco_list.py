"""Costco vs everything else on the shopping list, by voice."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from . import app, format as fmt, intent, lists
from .test_clarify import _SlotTestBase, _parsed

SHOPPING = [
    {"id": 1, "type": "shopping", "text": "quinoa", "store": "costco"},
    {"id": 2, "type": "shopping", "text": "diapers", "store": "costco"},
    {"id": 3, "type": "shopping", "text": "eggs", "store": None},
    {"id": 4, "type": "shopping", "text": "sriracha"},
]


class StoreInTest(unittest.TestCase):
    def test_costco(self):
        for text in ("what do we need at Costco", "what's on the costco list",
                     "add diapers to the Costco list", "read me the costco list"):
            self.assertEqual(intent.store_in(text), "costco", text)

    def test_everything_else(self):
        for text in ("what do we still need besides Costco",
                     "what's on the list that isn't Costco",
                     "what do we need after Costco",
                     "what do we need from other stores",
                     "what do we need other than at Costco"):
            self.assertEqual(intent.store_in(text), "other", text)

    def test_whole_list(self):
        self.assertIsNone(intent.store_in("what's on the shopping list"))


class FastParseTest(unittest.TestCase):
    def test_store_reads_skip_the_classifier(self):
        # The exact phrase that came back `unclear` twice on 2026-10-03.
        for text in ("What do we need at Costco?", "what's on the Costco list",
                     "do we need anything from costco", "what else do we need at costco",
                     "read me the costco list", "what do we need besides costco"):
            parsed = intent.fast_parse(text)
            self.assertIsNotNone(parsed, text)
            self.assertEqual(parsed["intent"], "show_shopping", text)

    def test_other_costco_questions_are_left_alone(self):
        for text in ("what time does costco close", "is costco open on sunday",
                     "add diapers to the costco list", "put wipes on the costco list"):
            self.assertIsNone(intent.fast_parse_store_list(text), text)


class WordingTest(unittest.TestCase):
    def test_store_read(self):
        self.assertEqual(fmt.summarize_store("costco", SHOPPING[:2]),
                         "2 items on the Costco list: quinoa and diapers.")
        self.assertEqual(fmt.summarize_store("costco", []), "Nothing on the Costco list.")

    def test_add_names_the_section(self):
        added = [{"type": "shopping", "text": "diapers"}]
        self.assertEqual(fmt.summarize_added(added, store="costco"),
                         "Added diapers to the Costco list.")
        self.assertEqual(fmt.summarize_added(added),
                         "Added diapers to the shopping list.")

    def test_companion_framing(self):
        self.assertEqual(lists.shopping_framed("add diapers to the Costco list"),
                         "add diapers to the shopping list")
        self.assertEqual(lists.in_store(SHOPPING, "other"), SHOPPING[2:])


class DispatchTest(_SlotTestBase):
    def test_reads_only_the_costco_section(self):
        with patch.object(app.intent_mod, "parse", new=AsyncMock()) as parse, \
             patch.object(app.lists_mod, "fetch", AsyncMock(return_value=SHOPPING)):
            result = self._run("what do we need at Costco")
        parse.assert_not_awaited()
        self.assertEqual(result["response"],
                         "2 items on the Costco list: quinoa and diapers.")
        self.assertEqual([it["id"] for it in result["items"]], [1, 2])
        # The kitchen display needs the section too: it pulls the full list
        # behind the view so its tabs work, and re-applies this filter.
        shown = [c for c in app.events.emit.await_args_list if c.args[0] == "show_list"]
        self.assertEqual(shown[-1].kwargs["store"], "costco")

    def test_plain_read_is_unchanged(self):
        with patch.object(app.intent_mod, "parse",
                          AsyncMock(return_value=_parsed(intent="show_shopping"))), \
             patch.object(app.lists_mod, "fetch", AsyncMock(return_value=SHOPPING)):
            result = self._run("what's on the shopping list")
        self.assertEqual(len(result["items"]), 4)

    def test_add_to_costco_files_and_teaches(self):
        stored = [{"id": 9, "type": "shopping", "text": "diapers"}]
        with patch.object(app.intent_mod, "parse",
                          AsyncMock(return_value=_parsed(intent="add_items"))), \
             patch.object(app.lists_mod, "add_from_text",
                          AsyncMock(return_value=stored)) as add, \
             patch.object(app.lists_mod, "set_store", AsyncMock()) as set_store, \
             patch.object(app, "_speaker_name", AsyncMock(return_value="brad")):
            result = self._run("add diapers to the Costco list")
        self.assertEqual(add.await_args.args[0], "add diapers to the shopping list")
        set_store.assert_awaited_once_with([9], "costco", "brad")
        self.assertEqual(result["response"], "Added diapers to the Costco list.")

    def test_failed_tag_does_not_claim_costco(self):
        stored = [{"id": 9, "type": "shopping", "text": "diapers"}]
        with patch.object(app.intent_mod, "parse",
                          AsyncMock(return_value=_parsed(intent="add_items"))), \
             patch.object(app.lists_mod, "add_from_text", AsyncMock(return_value=stored)), \
             patch.object(app.lists_mod, "set_store",
                          AsyncMock(side_effect=RuntimeError("down"))), \
             patch.object(app, "_speaker_name", AsyncMock(return_value="brad")):
            result = self._run("add diapers to the Costco list")
        self.assertEqual(result["response"], "Added diapers to the shopping list.")


if __name__ == "__main__":
    unittest.main()
