"""What the assistant SAYS when a list goes up on the kitchen screen.

Brad, 2026-07-27, after hearing his own to-dos read back: they are whole
sentences, and five of them in a row is a monologue nobody listens to. To-dos
and reminders are now counted aloud and shown on the display; shopping items
are two words each and stay spoken, because reading those back is the
hands-busy case the kitchen actually has.
"""

import unittest

from . import format as fmt


def _items(n, list_type, text="Calculate and set the Powerwall dynamic reserve to 50%"):
    return [{"type": list_type, "text": text, "due_at": None} for _ in range(n)]


class CountOnlyListsTest(unittest.TestCase):
    """To-dos and reminders: the count, then look at the screen."""

    def test_todos_are_counted_not_recited(self):
        said = fmt.summarize_list("todo", _items(4, "todo"))
        self.assertEqual(said, "You have 4 to-dos — they are on the screen.")
        self.assertNotIn("Powerwall", said)

    def test_reminders_are_counted_not_recited(self):
        said = fmt.summarize_list("reminder", _items(3, "reminder"))
        self.assertEqual(said, "You have 3 reminders — they are on the screen.")

    def test_singular_reads_naturally(self):
        self.assertEqual(fmt.summarize_list("todo", _items(1, "todo")),
                         "You have 1 to-do — it is on the screen.")
        self.assertEqual(fmt.summarize_list("reminder", _items(1, "reminder")),
                         "You have 1 reminder — it is on the screen.")

    def test_owner_is_named_first(self):
        self.assertEqual(
            fmt.summarize_list("todo", _items(2, "todo"), owner="adrienne"),
            "Adrienne, you have 2 to-dos — they are on the screen.")

    def test_empty_lists_read_correctly(self):
        # "your reminders is empty" was the grammar trap here.
        self.assertEqual(fmt.summarize_list("reminder", []), "You have no reminders.")
        self.assertEqual(fmt.summarize_list("todo", []), "Your to-do list is empty.")
        self.assertEqual(fmt.summarize_list("shopping", []),
                         "Your shopping list is empty.")
        self.assertEqual(fmt.summarize_list("reminder", [], owner="brad"),
                         "Brad, you have no reminders.")


class ShoppingStaysSpokenTest(unittest.TestCase):
    """Shopping items are short, and hearing them is the point."""

    def test_items_are_read_back(self):
        items = [{"type": "shopping", "text": "Buy eggs", "due_at": None},
                 {"type": "shopping", "text": "milk", "due_at": None}]
        said = fmt.summarize_list("shopping", items)
        self.assertEqual(said, "You have 2 items on your shopping list: eggs and milk.")

    def test_long_lists_are_truncated_aloud(self):
        items = [{"type": "shopping", "text": f"item{i}", "due_at": None}
                 for i in range(8)]
        said = fmt.summarize_list("shopping", items)
        self.assertIn("and 3 more", said)


if __name__ == "__main__":
    unittest.main()
