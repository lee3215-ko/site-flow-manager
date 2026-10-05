import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from app import Project, PublisherApp


class CompletedFilterTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.tree = ttk.Treeview(self.root)
        self.app = SimpleNamespace(
            tree=self.tree, search_var=tk.StringVar(self.root),
            completion_filter=tk.StringVar(self.root, value='진행 / 미완료'),
            date_filter=ttk.Combobox(self.root), visible_count=tk.Label(self.root),
            projects=[
                Project('pending', '', '', 'https://pending.test'),
                Project('one', '', '', 'https://one.test', crawl='완료 9개', crawl_completed_at='2026-09-13 12:00:00'),
                Project('two', '', '', 'https://two.test', crawl='완료 2개', crawl_completed_at='2026-09-12 09:00:00'),
                Project('legacy', '', '', 'https://legacy.test', crawl='완료 1개'),
            ], _update_summary=lambda: None,
        )
        self.app._insert_project_row = lambda p, parent, number: PublisherApp._insert_project_row(self.app, p, parent, number)
        self.app._update_selection_count = lambda *_: PublisherApp._update_selection_count(self.app)
        self.tree.bind('<<TreeviewSelect>>', self.app._update_selection_count)

    def refresh(self):
        PublisherApp._refresh_table(self.app)

    def test_pending_default_and_dates_descending(self):
        self.refresh()
        self.assertEqual(self.tree.get_children(), ('pending',))
        self.assertEqual(self.app.date_filter['values'][3:5], ('2026-09-13', '2026-09-12'))

    def test_date_and_search_filter(self):
        self.app.completion_filter.set('2026-09-13')
        self.refresh()
        self.assertEqual(self.tree.get_children(), ('one',))
        self.assertEqual(self.tree.parent('one'), '')
        self.app.search_var.set('not-found')
        self.refresh()
        self.assertEqual(self.tree.get_children(), ())

    def test_default_action_only_targets_visible_rows(self):
        self.refresh()
        self.assertEqual([p.name for p in PublisherApp._selected_many(self.app, default_all=True)], ['pending'])

    def test_legacy_and_all(self):
        self.app.completion_filter.set('이전 완료 · 날짜 미기록')
        self.refresh()
        self.assertEqual(self.tree.get_children(), ('legacy',))
        self.app.completion_filter.set('전체')
        self.refresh()
        self.assertEqual(len(self.tree.get_children()), 4)

    def test_row_numbers_follow_visible_order_and_filter(self):
        self.app.completion_filter.set('전체')
        self.refresh()
        self.assertEqual([self.tree.item(i, 'values')[0] for i in self.tree.get_children()], ['1', '2', '3', '4'])
        self.app.completion_filter.set('2026-09-12')
        self.refresh()
        self.assertEqual(self.tree.item('two', 'values')[:2], ('1', 'two'))

    def test_selection_counter_updates_on_event_and_refresh(self):
        self.app.completion_filter.set('전체')
        self.refresh()
        self.tree.selection_set(('pending', 'one'))
        self.root.update()
        self.assertEqual(self.app.visible_count['text'], '선택 2개 / 표시 4개 / 전체 4개')
        self.refresh()
        self.assertEqual(self.app.visible_count['text'], '선택 2개 / 표시 4개 / 전체 4개')
        self.app.completion_filter.set('진행 / 미완료')
        self.refresh()
        self.assertEqual(self.app.visible_count['text'], '선택 1개 / 표시 1개 / 전체 4개')
        self.app.search_var.set('missing')
        self.refresh()
        self.assertEqual(self.app.visible_count['text'], '선택 0개 / 표시 0개 / 전체 4개')

    def test_deselect_and_delete_update_counter(self):
        self.refresh()
        self.tree.selection_set('pending')
        self.root.update()
        self.tree.selection_remove('pending')
        self.root.update()
        self.assertEqual(self.app.visible_count['text'], '선택 0개 / 표시 1개 / 전체 4개')
        self.tree.selection_set('pending')
        self.app.projects.pop(0)
        self.refresh()
        self.assertEqual(self.app.visible_count['text'], '선택 0개 / 표시 0개 / 전체 3개')
