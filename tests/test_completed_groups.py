import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest

from app import Project, PublisherApp


class CompletedGroupTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.tree = ttk.Treeview(self.root)
        self.app = SimpleNamespace(
            tree=self.tree, search_var=tk.StringVar(self.root),
            projects=[
                Project('pending', '', '', 'https://pending.test'),
                Project('one', '', '', 'https://one.test', crawl='완료 9개', crawl_completed_at='2026-09-13 12:00:00'),
                Project('two', '', '', 'https://two.test', crawl='완료 2개', crawl_completed_at='2026-09-12 09:00:00'),
                Project('legacy', '', '', 'https://legacy.test', crawl='완료 1개'),
            ], _update_summary=lambda: None,
        )
        self.app._insert_project_row = lambda p, parent: PublisherApp._insert_project_row(self.app, p, parent)

    def refresh(self):
        PublisherApp._refresh_table(self.app)

    def test_pending_first_dates_descending_and_default_collapsed(self):
        self.refresh()
        self.assertEqual(self.tree.get_children(), ('pending', 'completed:2026-09-13', 'completed:2026-09-12', 'completed:unknown'))
        self.assertEqual(self.tree.parent('one'), 'completed:2026-09-13')
        self.assertFalse(self.tree.item('completed:2026-09-13', 'open'))
        self.assertIn('1개', self.tree.item('completed:2026-09-13', 'text'))

    def test_expanded_state_survives_refresh_and_search(self):
        self.refresh()
        self.tree.item('completed:2026-09-12', open=True)
        self.refresh()
        self.assertTrue(self.tree.item('completed:2026-09-12', 'open'))
        self.app.search_var.set('one.test')
        self.refresh()
        self.assertTrue(self.tree.item('completed:2026-09-13', 'open'))
        self.app.search_var.set('')
        self.refresh()
        self.assertFalse(self.tree.item('completed:2026-09-13', 'open'))
        self.assertTrue(self.tree.item('completed:2026-09-12', 'open'))

    def test_group_selection_does_not_select_all_projects(self):
        self.refresh()
        self.tree.selection_set('completed:2026-09-13')
        self.assertEqual(PublisherApp._selected_many(self.app, default_all=True), [])
        self.tree.selection_set('one')
        self.refresh()
        self.assertEqual([p.name for p in PublisherApp._selected_many(self.app)], ['one'])

    def test_failed_request_stays_ungrouped(self):
        self.app.projects[1].crawl = '실패'
        self.refresh()
        self.assertEqual(self.tree.parent('one'), '')
