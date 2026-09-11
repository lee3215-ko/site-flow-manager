from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import requests


API_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareError(RuntimeError):
    pass


class CloudflareClient:
    def __init__(self, account_id: str, api_token: str) -> None:
        self.account_id = account_id.strip()
        token = api_token.strip().strip('"').strip("'")
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        self.api_token = token
        self._projects_cache: list[dict] | None = None
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {self.api_token}", "Content-Type": "application/json"}
        )

    def _request(self, method: str, path: str, return_payload: bool = False, **kwargs):
        response = self.session.request(method, f"{API_BASE}{path}", timeout=30, **kwargs)
        try:
            payload = response.json()
        except ValueError as exc:
            raise CloudflareError(f"Cloudflare가 잘못된 응답을 반환했습니다 ({response.status_code}).") from exc
        if not response.ok or not payload.get("success", False):
            errors = payload.get("errors") or []
            detail = "; ".join(item.get("message", str(item)) for item in errors)
            codes = {str(item.get("code", "")) for item in errors}
            lowered = detail.lower()
            if "limit of projects" in lowered:
                raise CloudflareError(
                    "Cloudflare Pages 프로젝트 한도 100개에 도달했습니다. 기존 Pages 프로젝트를 삭제하거나 "
                    "다른 Cloudflare 계정의 Account ID와 API Token을 사용해야 합니다. 100개를 초과해 "
                    "운영하려면 Workers Static Assets 또는 Workers for Platforms 구성이 필요합니다."
                )
            if response.status_code in {401, 403} or codes & {"1000", "9109", "10000"}:
                if "permission" in lowered or "authorization" in lowered or response.status_code == 403:
                    message = (
                        "Cloudflare Pages 권한이 없습니다. API Token에 Account > Pages > Write 권한을 추가하고, "
                        "Account Resources에서 이 Account ID가 포함되었는지 확인해 주세요."
                    )
                else:
                    message = (
                        "API Token이 유효하지 않습니다. 토큰 이름이나 Token ID가 아니라, 생성 직후 한 번만 "
                        "표시되는 실제 Token 값을 입력해야 합니다. 앞뒤 공백도 확인해 주세요."
                    )
                raise CloudflareError(f"{message}\n\nCloudflare 응답: {detail or response.status_code}")
            raise CloudflareError(detail or f"Cloudflare 요청 실패 ({response.status_code})")
        return payload if return_payload else payload.get("result")

    def verify(self) -> list[dict]:
        if not self.account_id or not self.api_token:
            raise CloudflareError("Account ID와 API Token을 모두 입력해 주세요.")
        if not re.fullmatch(r"[0-9a-fA-F]{32}", self.account_id):
            raise CloudflareError("Account ID는 영문·숫자로 된 32자리 값이어야 합니다.")
        # Pages 목록 조회가 성공하면 토큰 유효성과 대상 계정의 Pages 권한을 동시에 확인할 수 있다.
        return self.list_projects()

    def list_projects(self, refresh: bool = False) -> list[dict]:
        if self._projects_cache is not None and not refresh:
            return list(self._projects_cache)

        projects: list[dict] = []
        page = 1
        while True:
            payload = self._request(
                "GET",
                f"/accounts/{self.account_id}/pages/projects",
                return_payload=True,
                params={"page": page, "per_page": 10},
            )
            projects.extend(payload.get("result") or [])
            info = payload.get("result_info") or {}
            total_pages = max(1, int(info.get("total_pages") or 1))
            if page >= total_pages:
                break
            page += 1
        self._projects_cache = projects
        return list(projects)

    def ensure_project(self, project_name: str) -> dict:
        projects = self.list_projects()
        existing = next(
            (item for item in projects if item.get("name") == project_name), None
        )
        if existing:
            return existing
        created = self._request(
            "POST",
            f"/accounts/{self.account_id}/pages/projects",
            json={"name": project_name, "production_branch": "main"},
        )
        if isinstance(created, dict):
            projects.append(created)
            self._projects_cache = projects
            return created
        raise CloudflareError("Cloudflare 프로젝트 생성 결과에서 주소를 확인하지 못했습니다.")

    def project_url(self, project_name: str) -> str:
        project = self.ensure_project(project_name)
        subdomain = str(project.get("subdomain") or "").strip()
        if not subdomain:
            raise CloudflareError(
                f"{project_name} 프로젝트의 실제 pages.dev 주소를 확인하지 못했습니다."
            )
        return f"https://{subdomain}"

    def deploy(self, project_name: str, site_root: Path) -> tuple[str, str]:
        self.ensure_project(project_name)
        npx = shutil.which("npx.cmd") or shutil.which("npx")
        if not npx:
            raise CloudflareError("Node.js의 npx를 찾지 못했습니다. Node.js LTS를 설치해 주세요.")
        env = os.environ.copy()
        env["CLOUDFLARE_API_TOKEN"] = self.api_token
        env["CLOUDFLARE_ACCOUNT_ID"] = self.account_id
        command = [
            npx,
            "--yes",
            "wrangler@4",
            "pages",
            "deploy",
            str(site_root),
            "--project-name",
            project_name,
            "--branch",
            "main",
            "--commit-dirty=true",
        ]
        process = subprocess.run(
            command,
            cwd=site_root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        output = "\n".join(part for part in (process.stdout, process.stderr) if part).strip()
        if process.returncode != 0:
            raise CloudflareError(output or "Wrangler 배포에 실패했습니다.")
        urls = re.findall(r"https://[a-zA-Z0-9._-]+\.pages\.dev", output)
        production_url = f"https://{project_name}.pages.dev"
        return (urls[-1] if urls else production_url), output


class CloudflareAccountPool:
    def __init__(self, accounts: list[dict], project_limit: int = 100) -> None:
        self.accounts = [dict(account) for account in accounts]
        self.project_limit = project_limit
        self.clients = [
            CloudflareClient(account.get("account_id", ""), account.get("api_token", ""))
            for account in self.accounts
        ]
        self.projects: list[list[dict] | None] = [None] * len(self.accounts)
        self.errors: list[str] = [""] * len(self.accounts)
        self.current_index = 0

    def refresh(self) -> list[dict]:
        statuses = []
        for index, (account, client) in enumerate(zip(self.accounts, self.clients)):
            try:
                projects = client.list_projects(refresh=True)
                self.projects[index] = projects
                self.errors[index] = ""
                statuses.append(
                    {
                        "name": account.get("name") or f"계정 {index + 1}",
                        "account_id": account.get("account_id", ""),
                        "count": len(projects),
                        "limit": self.project_limit,
                        "error": "",
                    }
                )
            except Exception as exc:
                self.projects[index] = None
                self.errors[index] = str(exc)
                statuses.append(
                    {
                        "name": account.get("name") or f"계정 {index + 1}",
                        "account_id": account.get("account_id", ""),
                        "count": None,
                        "limit": self.project_limit,
                        "error": str(exc),
                    }
                )
        return statuses

    def _ensure_loaded(self) -> None:
        if all(projects is None for projects in self.projects) and not any(self.errors):
            self.refresh()

    def statuses(self) -> list[dict]:
        result = []
        for index, account in enumerate(self.accounts):
            projects = self.projects[index]
            result.append(
                {
                    "name": account.get("name") or f"계정 {index + 1}",
                    "account_id": account.get("account_id", ""),
                    "count": len(projects) if projects is not None else None,
                    "limit": self.project_limit,
                    "error": self.errors[index],
                }
            )
        return result

    def record_project(self, account_id: str, project_name: str) -> None:
        for index, account in enumerate(self.accounts):
            if account.get("account_id") != account_id:
                continue
            projects = self.projects[index]
            if projects is not None and not any(
                project.get("name") == project_name for project in projects
            ):
                projects.append({"name": project_name})
                self.clients[index]._projects_cache = list(projects)
            return

    def client_for(self, project_name: str) -> tuple[CloudflareClient, dict]:
        if not self.accounts:
            raise CloudflareError("Cloudflare 계정을 한 개 이상 등록해 주세요.")
        self._ensure_loaded()

        for index, projects in enumerate(self.projects):
            if projects is not None and any(
                project.get("name") == project_name for project in projects
            ):
                self.current_index = index
                return self.clients[index], self.accounts[index]

        for offset in range(len(self.accounts)):
            index = (self.current_index + offset) % len(self.accounts)
            projects = self.projects[index]
            if projects is not None and len(projects) < self.project_limit:
                self.current_index = index
                return self.clients[index], self.accounts[index]

        details = "; ".join(
            f"{self.accounts[index].get('name') or f'계정 {index + 1}'}: {error}"
            for index, error in enumerate(self.errors)
            if error
        )
        if details:
            raise CloudflareError(f"사용 가능한 Cloudflare 계정이 없습니다. {details}")
        raise CloudflareError(
            f"등록된 Cloudflare 계정 {len(self.accounts)}개의 Pages 프로젝트가 모두 "
            f"{self.project_limit}개 한도에 도달했습니다."
        )
