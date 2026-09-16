from __future__ import annotations

import json
import os
import random
import queue
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
import tkinter as tk
from tkinter import ttk

from cloudflare_client import CloudflareAccountPool, CloudflareClient
from naver_browser import NaverBrowser, automation_self_test, settings_page_self_test, session_failure
from naver_tools import check_public_files, load_manifest, verify_public_deployment
from secret_store import load_secret, save_secret
from version import APP_VERSION
from updater import latest_release, prepare_update, start_helper, apply_update, RELEASES_URL
from site_builder import (
    archived_zip_path,
    install_verification_file,
    move_zip_to_success,
    prepare_zip,
    read_site_title,
    project_name_from_zip,
)


APP_TITLE = f"사이트 배포 · 네이버 자동 등록 관리자 v{APP_VERSION}"
TOKEN_HELP_URL = "https://dash.cloudflare.com/profile/api-tokens"
NAVER_DASHBOARD = "https://searchadvisor.naver.com/console/board"
APP_DIR = Path(os.getenv("LOCALAPPDATA", Path.home())) / "CodexSitePublisher"
PROJECTS_DIR = APP_DIR / "projects"
NAVER_PROFILE_DIR = APP_DIR / "naver-chromium-state"
DATA_FILE = APP_DIR / "projects.json"
SETTINGS_FILE = APP_DIR / "settings.json"
TOKEN_FILE = APP_DIR / "cloudflare-token.bin"


@dataclass
class Project:
    name: str
    zip_path: str
    site_root: str
    url: str
    pages: int = 0
    title: str = ""
    registration: str = "대기"
    verification_file: str = ""
    cloudflare: str = "준비"
    deployment_verified: str = "대기"
    cloudflare_account: str = ""
    ownership: str = "대기"
    naver_account: str = ""
    frequency: str = "대기"
    robots: str = "대기"
    sitemap: str = "대기"
    crawl: str = "대기"
    crawl_completed_at: str = ""
    updated_at: str = ""
    last_error: str = ""


class PublisherApp(tk.Tk):
    COLORS = {
        "bg": "#F3F6FB", "panel": "#FFFFFF", "nav": "#10233F", "nav2": "#17375E",
        "primary": "#2563EB", "success": "#059669", "warning": "#D97706",
        "danger": "#DC2626", "text": "#172033", "muted": "#667085", "line": "#DCE3EC",
    }

    def __init__(self) -> None:
        super().__init__()
        APP_DIR.mkdir(parents=True, exist_ok=True)
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        self.title(APP_TITLE)
        self.geometry("1540x920")
        self.minsize(1260, 760)
        self.configure(bg=self.COLORS["bg"])
        self.projects = self._load_projects()
        self.settings = self._load_settings()
        self.cloudflare_statuses: list[dict] = []
        self.naver_site_count: tuple[int, int] | None = None
        self.naver_count_error = ""
        self.busy = False
        self.closing = False
        self.cancel_event = threading.Event()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="site-publisher")
        self.naver_browser: NaverBrowser | None = None
        self.update_check_running = False
        self._configure_style()
        self._build_ui()
        self._refresh_table()
        self._append_log(f"프로그램 버전 v{APP_VERSION} 실행")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(700, self.refresh_service_counts)
        self.after(3000, lambda: self.check_updates(manual=False))

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF", foreground=self.COLORS["text"], rowheight=43, borderwidth=0, font=("Malgun Gothic", 9))
        style.configure("Treeview.Heading", background="#EAF0F8", foreground="#344054", relief="flat", font=("Malgun Gothic", 9, "bold"), padding=(7, 11))
        style.map("Treeview", background=[("selected", "#DBEAFE")], foreground=[("selected", "#10233F")])
        style.configure("TProgressbar", troughcolor="#E5EAF1", background=self.COLORS["primary"], borderwidth=0)

    def _build_ui(self) -> None:
        nav = tk.Frame(self, bg=self.COLORS["nav"], width=230)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        tk.Label(nav, text="SITE FLOW", bg=self.COLORS["nav"], fg="#FFFFFF", font=("Segoe UI", 20, "bold")).pack(anchor="w", padx=24, pady=(28, 4))
        tk.Label(nav, text="다중 사이트 자동 등록", bg=self.COLORS["nav"], fg="#9FB3CC", font=("Malgun Gothic", 9)).pack(anchor="w", padx=24, pady=(0, 28))
        self._nav_button(nav, "대시보드", self._refresh_table, active=True)
        self._nav_button(nav, "ZIP 여러 개 추가", self.add_zips)
        self._nav_button(nav, "선택 사이트 SEO 재배포", self.redeploy_seo)
        self._nav_button(nav, "Cloudflare 설정", self.open_settings)
        self._nav_button(nav, "네이버 로그인 저장", self.open_naver_login)
        self._nav_button(nav, "연결 현황 새로고침", self.refresh_service_counts)
        self._nav_button(nav, "네이버 등록 수 확인", self.refresh_naver_count)
        self._nav_button(nav, "버전 확인 / 업데이트", self.check_updates)
        tk.Frame(nav, bg="#294363", height=1).pack(fill="x", padx=20, pady=22)
        tk.Label(nav, text="자동 처리 순서", bg=self.COLORS["nav"], fg="#9FB3CC", font=("Malgun Gothic", 9, "bold")).pack(anchor="w", padx=24)
        for step in ["1  ZIP 이름 → 사이트 주소", "2  네이버 사이트 등록", "3  확인 HTML 다운로드", "4  HTML 삽입 후 배포", "5  캡차는 직접 완료", "6  검색 요청 자동 실행"]:
            tk.Label(nav, text=step, bg=self.COLORS["nav"], fg="#D7E1EE", font=("Malgun Gothic", 9)).pack(anchor="w", padx=25, pady=5)
        tk.Label(nav, text="먼저 '네이버 로그인 저장'에서 한 번 로그인하면 이후 자동화 창에 로그인 상태가 적용됩니다.", wraplength=180, justify="left", bg=self.COLORS["nav"], fg="#8297B1", font=("Malgun Gothic", 8)).pack(side="bottom", anchor="w", padx=24, pady=22)

        main = tk.Frame(self, bg=self.COLORS["bg"])
        main.pack(side="left", fill="both", expand=True)
        header = tk.Frame(main, bg=self.COLORS["panel"], height=74, highlightthickness=1, highlightbackground=self.COLORS["line"])
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text=f"다중 사이트 작업 현황  v{APP_VERSION}", bg=self.COLORS["panel"], fg=self.COLORS["text"], font=("Malgun Gothic", 18, "bold")).pack(side="left", padx=28)
        status_box = tk.Frame(header, bg=self.COLORS["panel"])
        status_box.pack(side="right", padx=28)
        self.connection_label = tk.Label(status_box, text="● Cloudflare 미설정", bg=self.COLORS["panel"], fg=self.COLORS["warning"], font=("Malgun Gothic", 9, "bold"))
        self.connection_label.pack(anchor="e")
        self.naver_count_label = tk.Label(status_box, text="● 네이버 사이트 확인 전", bg=self.COLORS["panel"], fg=self.COLORS["muted"], font=("Malgun Gothic", 9, "bold"))
        self.naver_count_label.pack(anchor="e", pady=(3, 0))

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill='both', expand=True)
        dashboard = tk.Frame(self.notebook, bg=self.COLORS['bg'])
        log_tab = tk.Frame(self.notebook, bg=self.COLORS['panel'])
        self.notebook.add(dashboard, text='사이트 목록')
        self.notebook.add(log_tab, text='작업 기록 (로그)')
        content = tk.Frame(dashboard, bg=self.COLORS["bg"])
        content.pack(fill="both", expand=True, padx=24, pady=18)
        cards = tk.Frame(content, bg=self.COLORS["bg"])
        cards.pack(fill="x")
        self.card_values = {}
        for title, key, color in [("전체 ZIP", "total", "#2563EB"), ("배포 완료", "deployed", "#059669"), ("캡차 대기", "captcha", "#D97706"), ("후속 작업 완료", "done", "#7C3AED")]:
            card = tk.Frame(cards, bg=self.COLORS["panel"], highlightthickness=1, highlightbackground=self.COLORS["line"])
            card.pack(side="left", fill="x", expand=True, padx=(0, 12))
            tk.Frame(card, bg=color, width=5).pack(side="left", fill="y")
            body = tk.Frame(card, bg=self.COLORS["panel"])
            body.pack(side="left", fill="both", expand=True, padx=17, pady=12)
            tk.Label(body, text=title, bg=self.COLORS["panel"], fg=self.COLORS["muted"], font=("Malgun Gothic", 9)).pack(anchor="w")
            value = tk.Label(body, text="0", bg=self.COLORS["panel"], fg=self.COLORS["text"], font=("Segoe UI", 22, "bold"))
            value.pack(anchor="w")
            self.card_values[key] = value

        queue = tk.Frame(content, bg=self.COLORS["panel"], highlightthickness=1, highlightbackground=self.COLORS["line"])
        queue.pack(fill="x", pady=(15, 10))
        tk.Label(queue, text="배포 간 랜덤 지연", bg=self.COLORS["panel"], fg=self.COLORS["text"], font=("Malgun Gothic", 10, "bold")).pack(side="left", padx=(18, 12), pady=11)
        self.delay_min_var = tk.StringVar(value=str(self.settings.get("delay_min", 0)))
        self.delay_max_var = tk.StringVar(value=str(self.settings.get("delay_max", 0)))
        self._delay_entry(queue, self.delay_min_var).pack(side="left")
        tk.Label(queue, text="분  ~", bg=self.COLORS["panel"], fg=self.COLORS["muted"], font=("Malgun Gothic", 9)).pack(side="left", padx=7)
        self._delay_entry(queue, self.delay_max_var).pack(side="left")
        tk.Label(queue, text="분", bg=self.COLORS["panel"], fg=self.COLORS["muted"], font=("Malgun Gothic", 9)).pack(side="left", padx=(7, 18))
        tk.Label(queue, text="각 사이트 배포가 끝난 뒤 이 범위에서 무작위로 기다립니다.", bg=self.COLORS["panel"], fg=self.COLORS["muted"], font=("Malgun Gothic", 9)).pack(side="left")
        self._button(queue, "작업 중지", self.stop_batch, danger=True).pack(side="right", padx=14, pady=7)

        toolbar = tk.Frame(content, bg=self.COLORS["bg"])
        toolbar.pack(fill="x", pady=(2, 9))
        self._button(toolbar, "+ ZIP 여러 개", self.add_zips, primary=True).pack(side="left")
        self._button(toolbar, "1단계  네이버 등록 → HTML → 배포", self.run_registration_batch).pack(side="left", padx=7)
        self._button(toolbar, "소유확인 완료 표시", self.mark_verified).pack(side="left", padx=7)
        self._button(toolbar, "2단계  네이버 후속 작업", self.run_post_batch).pack(side="left", padx=7)
        self._button(toolbar, "사이트 주소 복사", self.copy_selected_urls).pack(side="left", padx=7)
        self._button(toolbar, "선택 목록 삭제", self.delete_selected_projects, danger=True).pack(side="left", padx=7)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._refresh_table())
        search = tk.Entry(toolbar, textvariable=self.search_var, width=23, relief="flat", font=("Malgun Gothic", 10), highlightthickness=1, highlightbackground=self.COLORS["line"])
        search.pack(side="right", ipady=7)
        tk.Label(toolbar, text="검색", bg=self.COLORS["bg"], fg=self.COLORS["muted"], font=("Malgun Gothic", 9)).pack(side="right", padx=8)

        filters = tk.Frame(content, bg=self.COLORS['bg'])
        filters.pack(fill='x', pady=(0, 8))
        tk.Label(filters, text='목록 보기', bg=self.COLORS['bg']).pack(side='left', padx=6)
        self.completion_filter = tk.StringVar(value='진행 / 미완료')
        self.date_filter = ttk.Combobox(filters, textvariable=self.completion_filter, state='readonly', width=35)
        self.date_filter.pack(side='left')
        self.date_filter.bind('<<ComboboxSelected>>', lambda event: self._refresh_table())
        self.visible_count = tk.Label(filters, bg=self.COLORS['bg'])
        self.visible_count.pack(side='left', padx=12)
        self.deploy_only_var = tk.BooleanVar(value=False)
        tk.Checkbutton(filters, text='네이버 없이 배포만 (1단계)', variable=self.deploy_only_var,
                       bg=self.COLORS['bg']).pack(side='right', padx=8)
        table_panel = tk.Frame(content, bg=self.COLORS["panel"], highlightthickness=1, highlightbackground=self.COLORS["line"])
        table_panel.pack(fill="both", expand=True)
        columns = ("name", "url", "pages", "registration", "cloudflare", "cloudflare_account", "ownership", "naver_account", "frequency", "robots", "sitemap", "crawl")
        columns = ("name", "title") + columns[1:]
        self.tree = ttk.Treeview(table_panel, columns=columns, show="headings", selectmode="extended")
        self.tree.heading('#0', text='완료일별 그룹')
        self.tree.column('#0', width=245, minwidth=160, stretch=False)
        self.tree.tag_configure('completed_group', background='#E8EFF9', foreground='#17375E')
        headings = {"name": "ZIP/프로젝트", "url": "인식한 사이트 주소", "pages": "페이지", "registration": "네이버 등록", "cloudflare": "배포", "cloudflare_account": "CF 계정", "ownership": "소유확인", "naver_account": "네이버 아이디", "frequency": "수집 주기", "robots": "ROBOTS", "sitemap": "사이트맵", "crawl": "페이지 요청"}
        widths = {"name": 125, "url": 190, "pages": 45, "registration": 78, "cloudflare": 72, "cloudflare_account": 82, "ownership": 72, "naver_account": 110, "frequency": 82, "robots": 68, "sitemap": 68, "crawl": 82}
        for column in columns:
            if column == "title":
                headings[column] = "사이트 타이틀"
                widths[column] = 260
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], minwidth=55, stretch=False, anchor="w" if column in {"name", "url", "title"} else "center")
        scroll = ttk.Scrollbar(table_panel, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        horizontal = ttk.Scrollbar(table_panel, orient="horizontal", command=self.tree.xview)
        self.tree.configure(xscrollcommand=horizontal.set)
        table_panel.rowconfigure(0, weight=1)
        table_panel.columnconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<Double-1>", self._table_double_click)
        self.tree.bind("<Control-c>", self.copy_selected_urls)
        self.tree.bind("<Delete>", lambda _event: self.delete_selected_projects())

        log_panel = tk.Frame(log_tab, bg=self.COLORS["panel"], highlightthickness=1, highlightbackground=self.COLORS["line"])
        log_panel.pack(fill="both", expand=True, padx=12, pady=12)
        tk.Label(log_panel, text="작업 기록", bg=self.COLORS["panel"], fg=self.COLORS["text"], font=("Malgun Gothic", 9, "bold")).pack(anchor="w", padx=12, pady=(8, 2))
        self.log = tk.Text(log_panel, wrap='word', bg="#F8FAFC", fg="#475467", relief="flat", font=("Consolas", 11), state="disabled", padx=8, pady=6)
        log_scroll = ttk.Scrollbar(log_panel, orient='vertical', command=self.log.yview)
        log_scroll.pack(side='right', fill='y')
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 9))

        bottom = tk.Frame(content, bg=self.COLORS["bg"])
        bottom.pack(fill="x", pady=(10, 0))
        self.progress = ttk.Progressbar(bottom, mode="indeterminate", length=160)
        self.progress.pack(side="left")
        self.status_label = tk.Label(bottom, text="준비되었습니다.", bg=self.COLORS["bg"], fg=self.COLORS["muted"], font=("Malgun Gothic", 9))
        self.status_label.pack(side="left", padx=12)
        self._button(bottom, "오류 보기", self.show_error).pack(side="right")
        self._button(bottom, "사이트 열기", self.open_selected_site).pack(side="right", padx=7)
        self._button(bottom, "공개 파일 검사", self.check_selected).pack(side="right", padx=7)

    def _nav_button(self, parent, text, command, active=False):
        bg = self.COLORS["nav2"] if active else self.COLORS["nav"]
        button = tk.Button(parent, text=text, command=command, anchor="w", relief="flat", bd=0, bg=bg, activebackground=self.COLORS["nav2"], fg="#FFFFFF", activeforeground="#FFFFFF", font=("Malgun Gothic", 10, "bold" if active else "normal"), padx=24, pady=12, cursor="hand2")
        button.pack(fill="x", padx=10, pady=2)
        return button

    def _button(self, parent, text, command, primary=False, danger=False):
        bg = self.COLORS["primary"] if primary else (self.COLORS["danger"] if danger else "#FFFFFF")
        fg = "#FFFFFF" if primary or danger else self.COLORS["text"]
        return tk.Button(parent, text=text, command=command, relief="flat", bd=0, bg=bg, activebackground="#1D4ED8" if primary else ("#B91C1C" if danger else "#EAF0F8"), fg=fg, activeforeground=fg, font=("Malgun Gothic", 9, "bold"), padx=13, pady=8, cursor="hand2", highlightthickness=1, highlightbackground=bg if primary or danger else self.COLORS["line"])

    def _delay_entry(self, parent, variable):
        validate = (self.register(lambda value: value == "" or (value.isdigit() and int(value) <= 1440)), "%P")
        return tk.Entry(parent, textvariable=variable, width=5, justify="center", relief="solid", bd=1, font=("Segoe UI", 10), validate="key", validatecommand=validate)

    def _load_projects(self) -> list[Project]:
        if not DATA_FILE.exists():
            return []
        try:
            allowed = {field.name for field in fields(Project)}
            projects = []
            for item in json.loads(DATA_FILE.read_text(encoding="utf-8")):
                project = Project(
                    **{key: value for key, value in item.items() if key in allowed}
                )
                if not project.title:
                    project.title = read_site_title(Path(project.site_root))
                if (
                    "deployment_verified" not in item
                    and project.cloudflare == "완료"
                ):
                    project.cloudflare = "공개 확인 필요"
                projects.append(project)
            return projects
        except Exception:
            return []

    def _load_settings(self) -> dict:
        defaults = {
            "cloudflare_accounts": [],
            "delay_min": 0,
            "delay_max": 0,
            "last_naver_account": "",
        }
        loaded = {}
        if SETTINGS_FILE.exists():
            try:
                loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                defaults.update(
                    {
                        key: loaded.get(key, defaults[key])
                        for key in ("delay_min", "delay_max", "last_naver_account")
                    }
                )
            except Exception:
                pass

        encrypted = load_secret(TOKEN_FILE)
        try:
            token_map = json.loads(encrypted) if encrypted else {}
            if not isinstance(token_map, dict):
                token_map = {}
        except json.JSONDecodeError:
            token_map = {}

        public_accounts = loaded.get("cloudflare_accounts") or []
        for index, account in enumerate(public_accounts):
            account_id = str(account.get("account_id", "")).strip()
            if account_id:
                defaults["cloudflare_accounts"].append(
                    {
                        "name": account.get("name") or f"계정 {index + 1}",
                        "account_id": account_id,
                        "api_token": token_map.get(account_id, ""),
                    }
                )

        if not defaults["cloudflare_accounts"] and loaded.get("account_id"):
            legacy_token = token_map.get(loaded["account_id"], "") or encrypted
            defaults["cloudflare_accounts"].append(
                {
                    "name": "계정 1",
                    "account_id": loaded["account_id"],
                    "api_token": legacy_token,
                }
            )
        return defaults

    def _save(self) -> None:
        DATA_FILE.write_text(json.dumps([asdict(item) for item in self.projects], ensure_ascii=False, indent=2), encoding="utf-8")
        accounts = self.settings.get("cloudflare_accounts") or []
        public_settings = {
            "delay_min": self.settings.get("delay_min", 0),
            "delay_max": self.settings.get("delay_max", 0),
            "last_naver_account": self.settings.get("last_naver_account", ""),
            "cloudflare_accounts": [
                {"name": account.get("name", ""), "account_id": account.get("account_id", "")}
                for account in accounts
            ],
        }
        SETTINGS_FILE.write_text(json.dumps(public_settings, ensure_ascii=False, indent=2), encoding="utf-8")
        token_map = {
            account.get("account_id", ""): account.get("api_token", "")
            for account in accounts
            if account.get("account_id") and account.get("api_token")
        }
        save_secret(TOKEN_FILE, json.dumps(token_map, ensure_ascii=False))

    def _append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _status(self, message: str) -> None:
        def update():
            self.status_label.config(text=message)
            self._append_log(message)
        self.after(0, update)

    def _checkpoint(self, project: Project, message: str | None = None) -> None:
        project.updated_at = time.strftime("%Y-%m-%d %H:%M")
        self._save()
        self.after(0, self._refresh_table)
        if message:
            self._status(message)

    def _selected_many(self, default_all=False) -> list[Project]:
        names = set(self.tree.selection())
        if names:
            return [project for project in self.projects if project.name in names]
        visible = set(self.tree.get_children())
        return [project for project in self.projects if project.name in visible] if default_all else []

    def _selected_one(self) -> Project | None:
        selected = self._selected_many()
        if not selected:
            messagebox.showinfo(APP_TITLE, "먼저 사이트를 선택해 주세요.")
            return None
        return selected[0]

    def _table_double_click(self, event):
        row = self.tree.identify_row(event.y)
        if row and not row.startswith('completed:'):
            self.open_selected_site()

    def _refresh_table(self) -> None:
        selected = self.tree.selection() if hasattr(self, "tree") else ()
        position = self.tree.yview()
        for row in self.tree.get_children():
            self.tree.delete(row)
        query = self.search_var.get().strip().lower() if hasattr(self, "search_var") else ""
        dates = sorted({p.crawl_completed_at[:10] for p in self.projects if p.crawl.startswith('완료') and p.crawl_completed_at}, reverse=True)
        choices = ['진행 / 미완료', '전체', '완료 전체'] + dates
        if any(p.crawl.startswith('완료') and not p.crawl_completed_at for p in self.projects):
            choices.append('이전 완료 · 날짜 미기록')
        self.date_filter.configure(values=choices)
        mode = self.completion_filter.get()
        if mode not in choices:
            mode = '진행 / 미완료'
            self.completion_filter.set(mode)
        count = 0
        for project in self.projects:
            completed = project.crawl.startswith('완료')
            if mode == '진행 / 미완료' and completed:
                continue
            if mode == '완료 전체' and not completed:
                continue
            if mode not in {'진행 / 미완료', '전체', '완료 전체'}:
                day = project.crawl_completed_at[:10] or '이전 완료 · 날짜 미기록'
                if not completed or day != mode:
                    continue
            if query and all(
                query not in value.lower()
                for value in (project.name, project.url, project.naver_account, project.title)
            ):
                continue
            self._insert_project_row(project, '')
            count += 1
        self.visible_count.configure(text=f'{count}개 표시 / 전체 {len(self.projects)}개')
        for item in selected:
            if self.tree.exists(item):
                self.tree.selection_add(item)
        if (mode, query) == getattr(self, '_table_query', None) and position:
            self.tree.yview_moveto(position[0])
        self._table_query = (mode, query)
        self._update_summary()

    def _insert_project_row(self, project, parent):
        self.tree.insert(parent, 'end', iid=project.name, values=(project.name, project.title or '제목 없음', project.url, project.pages, project.registration, project.cloudflare, project.cloudflare_account, project.ownership, project.naver_account, project.frequency, project.robots, project.sitemap, project.crawl))

    def _update_summary(self) -> None:
        if not hasattr(self, "card_values"):
            return
        self.card_values["total"].config(text=str(len(self.projects)))
        self.card_values["deployed"].config(
            text=str(
                sum(
                    p.cloudflare == "완료" and p.deployment_verified == "완료"
                    for p in self.projects
                )
            )
        )
        self.card_values["captcha"].config(text=str(sum(p.ownership == "캡차 대기" for p in self.projects)))
        self.card_values["done"].config(text=str(sum(p.crawl.startswith("완료") for p in self.projects)))
        self._update_service_labels()

    def _cloudflare_accounts(self) -> list[dict]:
        return [
            dict(account)
            for account in self.settings.get("cloudflare_accounts", [])
            if account.get("account_id") and account.get("api_token")
        ]

    def _update_service_labels(self) -> None:
        if not hasattr(self, "connection_label"):
            return
        account_count = len(self.settings.get("cloudflare_accounts", []))
        known = [
            status for status in self.cloudflare_statuses if status.get("count") is not None
        ]
        if known:
            used = sum(status["count"] for status in known)
            capacity = sum(status["limit"] for status in known)
            failed = len(self.cloudflare_statuses) - len(known)
            suffix = f" · 오류 {failed}개" if failed else ""
            self.connection_label.config(
                text=f"● Cloudflare {account_count}계정 · Pages {used}/{capacity}{suffix}",
                fg=self.COLORS["success"] if not failed else self.COLORS["warning"],
            )
        elif account_count:
            self.connection_label.config(
                text=f"● Cloudflare {account_count}계정 · 사용량 확인 전",
                fg=self.COLORS["warning"],
            )
        else:
            self.connection_label.config(
                text="● Cloudflare 미설정", fg=self.COLORS["warning"]
            )

        if self.naver_site_count:
            count, limit = self.naver_site_count
            self.naver_count_label.config(
                text=f"● 네이버 등록 사이트 {count}/{limit}",
                fg=self.COLORS["success"] if count < limit else self.COLORS["warning"],
            )
        elif self.naver_count_error:
            self.naver_count_label.config(
                text="● 네이버 사이트 수 확인 실패", fg=self.COLORS["warning"]
            )
        else:
            self.naver_count_label.config(
                text="● 네이버 사이트 확인 전", fg=self.COLORS["muted"]
            )

    def refresh_service_counts(self) -> None:
        accounts = self._cloudflare_accounts()

        def check():
            cloudflare_statuses = (
                CloudflareAccountPool(accounts).refresh() if accounts else []
            )
            return cloudflare_statuses, self.naver_site_count, self.naver_count_error

        def done(result):
            self.cloudflare_statuses, self.naver_site_count, self.naver_count_error = result
            self._update_service_labels()
            for status in self.cloudflare_statuses:
                if status["count"] is None:
                    self._append_log(
                        f"Cloudflare {status['name']} 확인 실패: {status['error']}"
                    )
                else:
                    self._append_log(
                        f"Cloudflare {status['name']}: Pages {status['count']}/{status['limit']}"
                    )
            if self.naver_site_count:
                self._append_log(
                    f"네이버 등록 사이트: {self.naver_site_count[0]}/{self.naver_site_count[1]}"
                )
            elif self.naver_count_error:
                self._append_log(f"네이버 사이트 수 확인 실패: {self.naver_count_error}")

        self._background("Cloudflare 등록 수를 확인하는 중...", check, done)

    def refresh_naver_count(self) -> None:
        def done(result):
            self.naver_site_count = result
            self.naver_count_error = ""
            self._update_service_labels()
        self._background(
            "네이버 등록 수를 확인하는 중...",
            lambda: self._get_naver_browser().count_registered_sites(), done,
        )

    def check_updates(self, manual=True) -> None:
        if os.environ.get("SITEFLOW_DEV") == "1":
            if manual:
                messagebox.showinfo(APP_TITLE, "개발자 모드: 소스 변경 시 자동 빌드됩니다. 바탕화면 바로가기로 다시 실행하면 최신 빌드를 사용합니다.")
            return
        if self.closing or self.update_check_running:
            return
        self.update_check_running = True
        result_queue = queue.Queue()

        def check():
            try:
                result_queue.put((latest_release(), None))
            except Exception as exc:
                result_queue.put((None, str(exc)))

        def poll():
            if self.closing:
                return
            try:
                release, error = result_queue.get_nowait()
            except queue.Empty:
                self.after(200, poll)
                return
            self.update_check_running = False
            if error:
                self._append_log(f"업데이트 확인 실패: {error}")
                if manual:
                    messagebox.showerror(APP_TITLE, f"업데이트 확인 실패: {error}")
                return
            if release is None:
                if manual:
                    messagebox.showinfo(APP_TITLE, f"현재 버전 v{APP_VERSION}은 최신 공개 버전입니다.")
                return
            self._offer_update(release)

        threading.Thread(target=check, daemon=True, name="update-check").start()
        self.after(200, poll)

    def _offer_update(self, release):
        if self.closing:
            return
        if self.busy:
            self.after(2000, lambda: self._offer_update(release))
            return
        if not messagebox.askyesno(
            APP_TITLE,
            f"현재 버전: v{APP_VERSION}\n새 버전: v{release['version']}\n\n"
            "지금 업데이트할까요? 다운로드와 검증 후 프로그램이 자동으로 재시작됩니다.\n"
            "사이트 목록과 계정 설정은 유지됩니다.",
        ):
            return
        if not getattr(sys, "frozen", False):
            webbrowser.open(RELEASES_URL)
            return

        def ready(plan):
            self._save()
            start_helper(plan)
            self._on_close()

        self._background("새 버전을 다운로드하고 검증하는 중...", lambda: prepare_update(release), ready)

    def _set_busy(self, busy: bool, text="") -> None:
        self.busy = busy
        self.progress.start(12) if busy else self.progress.stop()
        self.status_label.config(text=text or ("작업 중..." if busy else "준비되었습니다."))

    def _background(self, label, action, done=None) -> None:
        if self.closing:
            return
        if self.busy:
            messagebox.showinfo(APP_TITLE, "현재 작업이 끝날 때까지 기다리거나 '작업 중지'를 눌러 주세요.")
            return
        self.cancel_event.clear()
        self._set_busy(True, label)
        self._append_log(label)

        def runner():
            try:
                result = action()
                self.after(0, lambda: self._finish_background(result, done))
            except Exception as exc:
                self.after(0, lambda error=exc: self._fail_background(error))
        self.worker.submit(runner)

    def _finish_background(self, result, done) -> None:
        if self.closing:
            return
        self._set_busy(False, "작업이 완료되었습니다.")
        if done:
            done(result)
        self._save()
        self._refresh_table()

    def _fail_background(self, error: Exception) -> None:
        if self.closing:
            return
        self._set_busy(False, "작업에 실패했습니다.")
        self._append_log(f"오류: {error}")
        messagebox.showerror(APP_TITLE, str(error))

    def _get_naver_browser(self) -> NaverBrowser:
        if self.naver_browser:
            try:
                process_alive = (
                    self.naver_browser.browser_process is not None
                    and self.naver_browser.browser_process.poll() is None
                )
                if process_alive and self.naver_browser.browser.is_connected():
                    self.naver_browser.select_live_page()
                    return self.naver_browser
            except Exception:
                pass
            try:
                self.naver_browser.__exit__(None, None, None)
            except Exception:
                pass
            self.naver_browser = None
        browser = NaverBrowser(NAVER_PROFILE_DIR, self._status)
        browser.cancel_event = self.cancel_event
        browser.__enter__()
        self.naver_browser = browser
        return browser

    def _on_close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.cancel_event.set()
        self.withdraw()

        def close_browser():
            if self.naver_browser:
                try:
                    self.naver_browser.__exit__(None, None, None)
                finally:
                    self.naver_browser = None

        future = self.worker.submit(close_browser)

        def wait_for_close():
            if future.done():
                self.worker.shutdown(wait=False, cancel_futures=True)
                self.destroy()
            else:
                self.after(100, wait_for_close)

        wait_for_close()

    def stop_batch(self) -> None:
        if self.busy:
            self.cancel_event.set()
            self._status("중지 요청을 받았습니다. 현재 단계가 끝나면 멈춥니다.")

    def _delay_range(self) -> tuple[int, int]:
        try:
            minimum = int(self.delay_min_var.get() or "0")
            maximum = int(self.delay_max_var.get() or "0")
        except ValueError as exc:
            raise ValueError("지연시간은 분 단위 숫자로 입력해 주세요.") from exc
        if minimum < 0 or maximum < 0 or minimum > maximum:
            raise ValueError("랜덤 지연시간은 최소값이 최대값보다 작거나 같아야 합니다.")
        self.settings.update({"delay_min": minimum, "delay_max": maximum})
        self._save()
        return minimum * 60, maximum * 60

    def _wait_random_delay(self, minimum: int, maximum: int, next_name: str) -> bool:
        seconds = random.randint(minimum, maximum) if maximum else 0
        for remaining in range(seconds, 0, -1):
            if self.cancel_event.wait(1):
                return False
            minutes, secs = divmod(remaining, 60)
            if remaining == seconds or remaining <= 10 or remaining % 30 == 0:
                self._status(f"다음 ZIP ({next_name})까지 랜덤 대기 {minutes:02d}:{secs:02d}")
        return not self.cancel_event.is_set()

    def open_settings(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Cloudflare 다중 계정 설정")
        dialog.geometry("780x620")
        dialog.resizable(False, False)
        dialog.configure(bg="#FFFFFF")
        dialog.transient(self)
        dialog.grab_set()
        tk.Label(dialog, text="Cloudflare 다중 계정", bg="#FFFFFF", fg=self.COLORS["text"], font=("Malgun Gothic", 16, "bold")).pack(anchor="w", padx=28, pady=(22, 6))
        tk.Label(dialog, text="목록 순서대로 사용하며 Pages 100개가 차면 다음 계정으로 자동 전환합니다.", bg="#FFFFFF", fg=self.COLORS["muted"], font=("Malgun Gothic", 9)).pack(anchor="w", padx=28)
        tk.Label(dialog, text="Token 이름이나 Token ID가 아니라 생성 직후 표시되는 실제 Token 값이 필요합니다.", bg="#FFFFFF", fg=self.COLORS["warning"], font=("Malgun Gothic", 9, "bold")).pack(anchor="w", padx=28, pady=(7, 0))

        working = [dict(account) for account in self.settings.get("cloudflare_accounts", [])]
        selected_index = [None]
        list_panel = tk.Frame(dialog, bg="#FFFFFF")
        list_panel.pack(fill="x", padx=28, pady=(16, 8))
        account_list = ttk.Treeview(
            list_panel,
            columns=("name", "account_id", "usage"),
            show="headings",
            height=6,
            selectmode="browse",
        )
        account_list.heading("name", text="계정 이름")
        account_list.heading("account_id", text="Account ID")
        account_list.heading("usage", text="Pages 사용량")
        account_list.column("name", width=135, anchor="w")
        account_list.column("account_id", width=390, anchor="w")
        account_list.column("usage", width=120, anchor="center")
        account_list.pack(fill="x")

        form = tk.Frame(dialog, bg="#FFFFFF")
        form.pack(fill="x", padx=28, pady=8)
        tk.Label(form, text="계정 이름", bg="#FFFFFF", fg=self.COLORS["text"], font=("Malgun Gothic", 9, "bold")).grid(row=0, column=0, sticky="w", pady=7)
        name = tk.Entry(form, font=("Malgun Gothic", 10), relief="solid", bd=1)
        name.grid(row=0, column=1, sticky="ew", padx=(14, 0), ipady=6)
        tk.Label(form, text="Account ID", bg="#FFFFFF", fg=self.COLORS["text"], font=("Malgun Gothic", 9, "bold")).grid(row=1, column=0, sticky="w", pady=7)
        account = tk.Entry(form, font=("Consolas", 10), relief="solid", bd=1)
        account.grid(row=1, column=1, sticky="ew", padx=(14, 0), ipady=6)
        tk.Label(form, text="API Token", bg="#FFFFFF", fg=self.COLORS["text"], font=("Malgun Gothic", 9, "bold")).grid(row=2, column=0, sticky="w", pady=7)
        token = tk.Entry(form, font=("Consolas", 10), relief="solid", bd=1, show="●")
        token.grid(row=2, column=1, sticky="ew", padx=(14, 0), ipady=6)
        form.columnconfigure(1, weight=1)

        def usage_for(account_id: str) -> str:
            for status in self.cloudflare_statuses:
                if status.get("account_id") == account_id:
                    if status.get("count") is None:
                        return "연결 오류"
                    return f"{status['count']}/{status['limit']}"
            return "확인 전"

        def refresh_list(select: int | None = None):
            for item in account_list.get_children():
                account_list.delete(item)
            for index, item in enumerate(working):
                account_list.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(item.get("name", ""), item.get("account_id", ""), usage_for(item.get("account_id", ""))),
                )
            if select is not None and account_list.exists(str(select)):
                account_list.selection_set(str(select))

        def clear_form():
            selected_index[0] = None
            for entry in (name, account, token):
                entry.delete(0, "end")
            name.insert(0, f"계정 {len(working) + 1}")
            name.focus_set()

        def load_selected(_event=None):
            selection = account_list.selection()
            if not selection:
                return
            index = int(selection[0])
            selected_index[0] = index
            item = working[index]
            for entry, value in (
                (name, item.get("name", "")),
                (account, item.get("account_id", "")),
                (token, item.get("api_token", "")),
            ):
                entry.delete(0, "end")
                entry.insert(0, value)

        def upsert() -> bool:
            item = {
                "name": name.get().strip() or f"계정 {len(working) + 1}",
                "account_id": account.get().strip(),
                "api_token": token.get().strip(),
            }
            if len(item["account_id"]) != 32 or any(
                character not in "0123456789abcdefABCDEF" for character in item["account_id"]
            ):
                messagebox.showerror(APP_TITLE, "Account ID는 영문·숫자로 된 32자리 값이어야 합니다.", parent=dialog)
                return False
            if not item["api_token"]:
                messagebox.showerror(APP_TITLE, "API Token을 입력해 주세요.", parent=dialog)
                return False
            duplicate = next(
                (
                    index
                    for index, existing in enumerate(working)
                    if existing.get("account_id") == item["account_id"]
                    and index != selected_index[0]
                ),
                None,
            )
            if duplicate is not None:
                messagebox.showerror(APP_TITLE, "이미 등록된 Account ID입니다.", parent=dialog)
                return False
            if selected_index[0] is None:
                working.append(item)
                selected_index[0] = len(working) - 1
            else:
                working[selected_index[0]] = item
            refresh_list(selected_index[0])
            return True

        def delete_selected():
            selection = account_list.selection()
            if not selection:
                return
            working.pop(int(selection[0]))
            clear_form()
            refresh_list()

        def move_selected(direction: int):
            selection = account_list.selection()
            if not selection:
                return
            index = int(selection[0])
            target = index + direction
            if target < 0 or target >= len(working):
                return
            working[index], working[target] = working[target], working[index]
            selected_index[0] = target
            refresh_list(target)

        def test_selected():
            if not upsert():
                return
            item = working[selected_index[0]]

            def done(projects):
                self.cloudflare_statuses = [
                    status
                    for status in self.cloudflare_statuses
                    if status.get("account_id") != item["account_id"]
                ]
                self.cloudflare_statuses.append(
                    {
                        "name": item["name"],
                        "account_id": item["account_id"],
                        "count": len(projects),
                        "limit": 100,
                        "error": "",
                    }
                )
                refresh_list(selected_index[0])
                self._update_service_labels()
                messagebox.showinfo(
                    APP_TITLE,
                    f"{item['name']} 연결 성공\n현재 Pages 프로젝트: {len(projects)}/100",
                    parent=dialog,
                )

            self._background(
                f"Cloudflare {item['name']} 권한을 확인하는 중...",
                lambda: CloudflareClient(item["account_id"], item["api_token"]).verify(),
                done,
            )

        def save_settings():
            if any((account.get().strip(), token.get().strip())) and not upsert():
                return
            self.settings["cloudflare_accounts"] = working
            self._save()
            self._update_summary()
            dialog.destroy()
            self.after(100, self.refresh_service_counts)

        buttons = tk.Frame(dialog, bg="#FFFFFF")
        buttons.pack(fill="x", padx=28, pady=(5, 0))
        self._button(buttons, "저장", save_settings, primary=True).pack(side="right")
        self._button(buttons, "선택 연결 테스트", test_selected).pack(side="right", padx=8)
        self._button(buttons, "선택 삭제", delete_selected, danger=True).pack(side="left")
        self._button(buttons, "새 계정", clear_form).pack(side="left", padx=8)
        self._button(buttons, "위로", lambda: move_selected(-1)).pack(side="left", padx=(0, 5))
        self._button(buttons, "아래로", lambda: move_selected(1)).pack(side="left", padx=(0, 8))
        self._button(buttons, "API Token 만들기", lambda: webbrowser.open(TOKEN_HELP_URL)).pack(side="left")
        tk.Label(dialog, text="권한: Account · Pages · Write / 리소스: 해당 계정 포함", bg="#FFFFFF", fg=self.COLORS["muted"], font=("Malgun Gothic", 9)).pack(anchor="w", padx=28, pady=(16, 0))
        account_list.bind("<<TreeviewSelect>>", load_selected)
        refresh_list(0 if working else None)
        if working:
            load_selected()
        else:
            clear_form()

    def open_naver_login(self) -> None:
        def login():
            browser = self._get_naver_browser()
            browser.wait_for_login()
            return True

        def done(_result):
            messagebox.showinfo(
                APP_TITLE,
                "네이버 로그인을 확인하고 저장했습니다.\n이제 1단계 또는 2단계 작업을 실행하세요.",
            )

        self._background("네이버 자동화 로그인 창을 여는 중...", login, done)

    def add_zips(self) -> None:
        paths = filedialog.askopenfilenames(title="배포할 ZIP 파일 여러 개 선택", filetypes=[("ZIP 파일", "*.zip")])
        if not paths:
            return

        def prepare_all():
            added, skipped, errors = [], [], []
            existing = {project.name for project in self.projects}
            for raw_path in paths:
                try:
                    zip_path = Path(raw_path)
                    name = project_name_from_zip(zip_path)
                    if name in existing:
                        skipped.append(name)
                        continue
                    self._status(f"ZIP 준비 중: {zip_path.name} → https://{name}.pages.dev")
                    prepared = prepare_zip(zip_path, PROJECTS_DIR / name, name)
                    project = Project(name=name, zip_path=str(zip_path), site_root=str(prepared.root), url=f"https://{name}.pages.dev", pages=prepared.page_count, updated_at=time.strftime("%Y-%m-%d %H:%M"))
                    project.title = read_site_title(prepared.root)
                    self.projects.append(project)
                    existing.add(name)
                    added.append(name)
                    self._checkpoint(project)
                except Exception as exc:
                    errors.append(f"{Path(raw_path).name}: {exc}")
            return added, skipped, errors

        def done(result):
            added, skipped, errors = result
            message = f"ZIP {len(added)}개를 추가했습니다."
            if skipped:
                message += f"\n중복 제외: {len(skipped)}개"
            if errors:
                message += "\n\n오류:\n" + "\n".join(errors[:8])
            messagebox.showinfo(APP_TITLE, message)
        self._background("여러 ZIP 파일을 검사하고 사이트 주소로 인식하는 중...", prepare_all, done)

    def redeploy_seo(self) -> None:
        if self.busy:
            messagebox.showinfo(APP_TITLE, "현재 작업이 끝난 후 진행해 주세요.")
            return
        selected = self._selected_many(default_all=False)
        if not selected:
            messagebox.showinfo(APP_TITLE, "재배포할 사이트를 목록에서 선택해 주세요.")
            return
        if not messagebox.askokcancel(APP_TITLE, "선택 사이트의 작업용 파일을 보정하고 재배포합니다. ZIP 원본과 기존 네이버 소유확인 파일은 보존합니다. 진행할까요?"):
            return
        for project in selected:
            project.cloudflare = "재배포 필요"
            project.deployment_verified = "대기"
        self._save()
        self._refresh_table()
        self.run_registration_batch(selected)

    def run_registration_batch(self, selected_projects=None) -> None:
        deploy_only = self.deploy_only_var.get()
        try:
            delay_min, delay_max = self._delay_range()
        except ValueError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        selected = selected_projects if selected_projects is not None else self._selected_many(default_all=True)
        candidates = [
            project
            for project in selected
            if (not deploy_only and project.registration != "완료")
            or project.cloudflare != "완료"
            or project.deployment_verified != "완료"
            or archived_zip_path(Path(project.zip_path)) is None
        ]
        if not candidates:
            messagebox.showinfo(APP_TITLE, "등록하거나 배포할 ZIP이 없습니다.")
            return
        confirmation = ("네이버 로그인 없이 HTML 주소·사이트맵·robots.txt를 보정하고 Cloudflare에 배포·검증합니다. 네이버 등록과 소유확인은 수행하지 않습니다."
                        if deploy_only else "자동화 전용 Chromium이 열립니다. 처음이면 네이버 로그인을 직접 완료해 주세요.\n\n사이트 등록과 HTML 다운로드는 자동이며, 소유확인 캡차는 모든 배포가 끝난 뒤 직접 진행합니다.")
        if not messagebox.askokcancel(APP_TITLE, confirmation):
            return

        def run():
            from deployment_runtime import preflight
            self._status('배포 도구 실행을 사전 확인하는 중...')
            preflight()
            pool = CloudflareAccountPool(self._cloudflare_accounts())
            self.cloudflare_statuses = pool.refresh()
            pool.client_for(candidates[0].name)
            self.after(0, self._update_service_labels)
            completed, errors = [], []
            needs_naver = not deploy_only and any(
                project.registration != "완료"
                or not Path(project.verification_file).is_file()
                for project in candidates
            )
            browser = self._get_naver_browser() if needs_naver else None
            if candidates:
                for index, project in enumerate(candidates):
                    if self.cancel_event.is_set():
                        break
                    try:
                        client, account, actual_url = pool.project_for(project.name, self._status)
                        pool.record_project(
                            account.get("account_id", ""), project.name
                        )
                        self.cloudflare_statuses = pool.statuses()
                        self.after(0, self._update_service_labels)
                        if project.url.rstrip("/") != actual_url.rstrip("/"):
                            old_url = project.url
                            source_zip = archived_zip_path(Path(project.zip_path)) or Path(project.zip_path)
                            prepared = prepare_zip(
                                source_zip,
                                PROJECTS_DIR / project.name,
                                project.name,
                                base_url=actual_url,
                            )
                            project.url = actual_url
                            project.site_root = str(prepared.root)
                            project.pages = prepared.page_count
                            project.title = read_site_title(prepared.root)
                            project.registration = "대기"
                            project.verification_file = ""
                            project.cloudflare = "재배포 필요"
                            project.deployment_verified = "대기"
                            project.ownership = "대기"
                            self._checkpoint(
                                project,
                                f"Cloudflare 실제 주소 보정: {old_url} → {actual_url}",
                            )

                        if not deploy_only and (project.registration != "완료" or not Path(project.verification_file).is_file()):
                            if browser is None:
                                browser = self._get_naver_browser()
                            project.registration = "진행 중"
                            project.last_error = ""
                            self._checkpoint(project, f"[{index + 1}/{len(candidates)}] 네이버 등록: {project.url}")
                            verification = browser.register_and_download(project.url, PROJECTS_DIR / project.name / "verification")
                            install_verification_file(Path(project.site_root), verification)
                            project.verification_file = str(verification)
                            project.registration = "완료"
                            project.cloudflare = "재배포 필요"
                            project.deployment_verified = "대기"
                            project.ownership = "캡차 대기"
                            self._checkpoint(project, f"소유확인 HTML 삽입 완료: {verification.name}")

                        verification_path = None if deploy_only else Path(project.verification_file)
                        if project.cloudflare == "완료" and project.deployment_verified != "완료":
                            project.cloudflare = "공개 확인 중"
                            self._checkpoint(project, f"기존 배포 공개 파일 확인 중: {project.url}")
                            try:
                                verify_public_deployment(
                                    Path(project.site_root), verification_path, timeout_seconds=15
                                )
                                project.cloudflare = "완료"
                                project.deployment_verified = "완료"
                                self._checkpoint(project, f"공개 파일 확인 완료: {project.url}")
                            except Exception:
                                project.cloudflare = "재배포 필요"
                                project.deployment_verified = "실패"

                        if project.cloudflare != "완료" or project.deployment_verified != "완료":
                            project.cloudflare = "배포 중"
                            project.cloudflare_account = account.get("name", "")
                            account_status = next(
                                (
                                    status
                                    for status in pool.statuses()
                                    if status["account_id"] == account.get("account_id")
                                ),
                                None,
                            )
                            usage = (
                                f" ({account_status['count']}/{account_status['limit']})"
                                if account_status and account_status["count"] is not None
                                else ""
                            )
                            self._checkpoint(
                                project,
                                f"Cloudflare 배포 시작: {project.name} → {project.cloudflare_account}{usage}",
                            )
                            self._checkpoint(project, '배포 전 주소 보정 및 SEO 검사 중...')
                            from site_builder import write_seo_files
                            root = Path(project.site_root)
                            previous_manifest = load_manifest(root)
                            prepared = write_seo_files(root, actual_url, previous_manifest.get('indexnow_key'))
                            project.pages = prepared.page_count
                            if verification_path is not None and verification_path.resolve() != (root / verification_path.name).resolve():
                                install_verification_file(root, verification_path)
                            self._checkpoint(project, f'SEO 사전 검사 완료: 콘텐츠 {project.pages}개 / 소유확인 파일 보존')
                            deployment_url, _ = client.deploy(project.name, root)
                            pool.record_project(account.get("account_id", ""), project.name)
                            self.cloudflare_statuses = pool.statuses()
                            self.after(0, self._update_service_labels)
                            project.cloudflare = "공개 확인 중"
                            project.deployment_verified = "진행 중"
                            self._checkpoint(
                                project,
                                f"Cloudflare 명령 완료, 공개 파일 검증 중: {deployment_url}",
                            )
                            verify_public_deployment(
                                Path(project.site_root), verification_path
                            )
                            project.cloudflare = "완료"
                            project.deployment_verified = "완료"
                            if not deploy_only and project.ownership != "완료":
                                project.ownership = "캡차 대기"
                            self._checkpoint(project, f"배포 및 공개 파일 확인 완료: {project.url}")

                        archived = move_zip_to_success(Path(project.zip_path))
                        project.zip_path = str(archived)
                        self._checkpoint(
                            project, f"ZIP 성공 폴더 이동 완료: {archived}"
                        )
                        completed.append(project.name)
                    except Exception as exc:
                        project.last_error = str(exc)
                        if project.registration == "진행 중":
                            project.registration = "실패"
                        if project.cloudflare in {"배포 중", "공개 확인 중"}:
                            project.cloudflare = "공개 검증 실패"
                            project.deployment_verified = "실패"
                        self._checkpoint(project, f"{project.name} 실패: {exc}")
                        errors.append(f"{project.name}: {exc}")
                        continue

                    remaining = candidates[index + 1:]
                    if remaining and not self._wait_random_delay(delay_min, delay_max, remaining[0].name):
                        break
            return completed, errors, self.cancel_event.is_set()

        def done(result):
            completed, errors, canceled = result
            message = f"{'Cloudflare 배포만 완료' if deploy_only else '등록 파일 삽입 및 배포 완료'}: {len(completed)}개"
            if canceled:
                message += "\n사용자 요청으로 중지했습니다."
            if errors:
                message += f"\n실패: {len(errors)}개\n\n" + "\n".join(errors[:6])
            elif not deploy_only:
                message += "\n\n이제 네이버에서 각 사이트의 캡차 소유확인을 직접 완료하세요."
            messagebox.showinfo(APP_TITLE, message)
        self._background("네이버 없이 SEO 보정 → Cloudflare 배포를 시작합니다." if deploy_only else "네이버 등록 → 확인 HTML → Cloudflare 배포를 시작합니다.", run, done)

    def mark_verified(self) -> None:
        if self.busy:
            messagebox.showinfo(APP_TITLE, "현재 작업이 끝난 뒤 진행해 주세요.")
            return
        selected = self._selected_many()
        if not selected:
            selected = [
                p
                for p in self.projects
                if p.ownership == "캡차 대기" and p.deployment_verified == "완료"
            ]
        if not selected:
            messagebox.showinfo(APP_TITLE, "캡차 대기 상태의 사이트가 없습니다.")
            return
        unverified = [p.name for p in selected if p.deployment_verified != "완료"]
        if unverified:
            messagebox.showerror(
                APP_TITLE,
                "공개 파일 확인이 끝나지 않아 소유확인 완료로 표시할 수 없습니다:\n"
                + "\n".join(unverified[:10]),
            )
            return
        naver_account = simpledialog.askstring(
            APP_TITLE,
            f"선택한 {len(selected)}개 사이트의 소유확인을 진행한 네이버 아이디를 입력해 주세요.",
            initialvalue=self.settings.get("last_naver_account", ""),
            parent=self,
        )
        if naver_account is None:
            return
        naver_account = naver_account.strip()
        if not naver_account:
            messagebox.showerror(APP_TITLE, "네이버 아이디를 입력해 주세요.")
            return
        if not messagebox.askyesno(APP_TITLE, f"선택한 {len(selected)}개 사이트의 네이버 소유확인을 직접 완료했습니까?"):
            return
        self.settings["last_naver_account"] = naver_account
        for project in selected:
            project.ownership = "완료"
            project.naver_account = naver_account
            project.last_error = ""
        self._save()
        self._refresh_table()

    def run_post_batch(self) -> None:
        selected = self._selected_many(default_all=True)
        candidates = [p for p in selected if p.ownership == "완료" and not p.crawl.startswith("완료")]
        if not candidates:
            messagebox.showinfo(APP_TITLE, "소유확인 완료 후 후속 작업할 사이트가 없습니다.")
            return
        if not messagebox.askokcancel(APP_TITLE, f"{len(candidates)}개 사이트에 다음 작업을 자동 실행합니다.\n\n수집 주기 빠르게 → robots.txt 수집요청 → sitemap.xml 제출 → 페이지 URL별 수집요청"):
            return

        def run():
            completed, errors = [], []
            browser = self._get_naver_browser()
            if browser:
                for index, project in enumerate(candidates, start=1):
                    if self.cancel_event.is_set():
                        break
                    try:
                        verification_path = Path(project.verification_file)
                        if not verification_path.is_file():
                            raise RuntimeError("네이버 소유확인 HTML 파일을 찾지 못했습니다.")
                        verify_public_deployment(
                            Path(project.site_root),
                            verification_path,
                            timeout_seconds=15,
                        )
                        project.deployment_verified = "완료"
                        manifest = load_manifest(Path(project.site_root))
                        if self.cancel_event.is_set():
                            break
                        self._checkpoint(project, f"[{index}/{len(candidates)}] 네이버 후속 작업: {project.url}")
                        completed_steps = set()
                        if project.frequency in {"빠르게", "완료", "완료 (빠르게)"}:
                            completed_steps.add("frequency")
                        if project.robots in {"완료", "확인 완료"}:
                            completed_steps.add("robots")
                        if project.sitemap in {"완료", "복사 완료"}:
                            completed_steps.add("sitemap")
                        if project.crawl.startswith("완료"):
                            completed_steps.add("crawl")

                        def save_step(field_name: str, state: str) -> None:
                            labels = {
                                "frequency": "수집 주기",
                                "robots": "robots.txt",
                                "sitemap": "사이트맵",
                                "crawl": "페이지 요청",
                            }
                            if field_name == 'crawl':
                                if state.startswith('완료') and not project.crawl_completed_at:
                                    project.crawl_completed_at = time.strftime('%Y-%m-%d %H:%M:%S')
                                elif not state.startswith('완료'):
                                    project.crawl_completed_at = ''
                            setattr(project, field_name, state)
                            self._checkpoint(
                                project, f"{project.name} {labels[field_name]}: {state}"
                            )

                        count = browser.run_after_ownership(
                            project.url,
                            manifest.get("pages", []),
                            completed_steps=completed_steps,
                            on_step=save_step,
                        )
                        project.last_error = ""
                        self._checkpoint(project, f"네이버 후속 작업 완료: {project.name} ({count}개 페이지)")
                        completed.append(project.name)
                    except Exception as exc:
                        project.last_error = str(exc)
                        for field_name in ("frequency", "robots", "sitemap", "crawl"):
                            if getattr(project, field_name) == "진행 중":
                                setattr(project, field_name, "실패")
                        self._checkpoint(project, f"{project.name} 후속 작업 실패: {exc}")
                        errors.append(f"{project.name}: {exc}")
                        if session_failure(exc) or self.cancel_event.is_set():
                            self._status('인증·브라우저 오류 또는 중지 요청으로 후속 일괄 작업을 종료했습니다. 나머지 사이트는 처리하지 않았습니다.')
                            break
            return completed, errors, self.cancel_event.is_set()

        def done(result):
            completed, errors, canceled = result
            message = f"네이버 후속 작업 완료: {len(completed)}개"
            if canceled:
                message += "\n사용자 요청으로 중지했습니다."
            if errors:
                message += f"\n실패: {len(errors)}개\n\n" + "\n".join(errors[:6])
            messagebox.showinfo(APP_TITLE, message)
        self._background("네이버 후속 자동 작업을 시작합니다.", run, done)

    def check_selected(self) -> None:
        project = self._selected_one()
        if project:
            self._background("공개 파일을 검사하는 중...", lambda: check_public_files(Path(project.site_root)), lambda result: messagebox.showinfo(APP_TITLE, "\n".join(f"{key}: {value}" for key, value in result.items())))

    def show_error(self) -> None:
        project = self._selected_one()
        if project:
            messagebox.showinfo(APP_TITLE, project.last_error or "기록된 오류가 없습니다.")

    def copy_selected_urls(self, _event=None):
        selected = self._selected_many()
        if not selected:
            messagebox.showinfo(APP_TITLE, "주소를 복사할 사이트를 선택해 주세요.")
            return "break"
        text = "\n".join(project.url for project in selected)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()
        self._status(f"사이트 주소 {len(selected)}개를 클립보드에 복사했습니다.")
        return "break"

    def delete_selected_projects(self) -> None:
        if self.busy:
            messagebox.showinfo(APP_TITLE, "현재 작업이 끝난 뒤 삭제해 주세요.")
            return
        selected = self._selected_many()
        if not selected:
            messagebox.showinfo(APP_TITLE, "목록에서 삭제할 사이트를 선택해 주세요.")
            return
        if not messagebox.askyesno(
            APP_TITLE,
            f"선택한 {len(selected)}개 사이트를 프로그램 목록에서 삭제할까요?\n\n"
            "원본 ZIP, Cloudflare 프로젝트, 네이버 등록 사이트와 로컬 작업 파일은 삭제되지 않습니다.",
        ):
            return
        names = {project.name for project in selected}
        self.projects = [
            project for project in self.projects if project.name not in names
        ]
        self._save()
        self._refresh_table()
        self._status(f"사이트 {len(names)}개를 프로그램 목록에서 삭제했습니다.")

    def open_selected_site(self) -> None:
        project = self._selected_one()
        if project:
            webbrowser.open(project.url)


if __name__ == "__main__":
    if "--apply-update" in sys.argv:
        try:
            apply_update(Path(sys.argv[sys.argv.index("--apply-update") + 1]))
        except Exception as exc:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, str(exc), "Site Flow 업데이트 실패", 0x10)
            raise SystemExit(1)
    elif "--settings-page-self-test" in sys.argv:
        flag_index = sys.argv.index("--settings-page-self-test")
        if flag_index + 1 >= len(sys.argv):
            raise SystemExit(2)
        settings_page_self_test(NAVER_PROFILE_DIR, sys.argv[flag_index + 1])
    elif "--automation-real-profile-self-test" in sys.argv:
        automation_self_test(NAVER_PROFILE_DIR)
    elif "--automation-self-test" in sys.argv:
        automation_self_test()
    else:
        PublisherApp().mainloop()
