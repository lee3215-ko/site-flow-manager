# Site Flow

여러 정적 사이트 ZIP을 Cloudflare Pages에 배포하고 네이버 서치어드바이저 등록과 후속 요청을 관리하는 Windows 프로그램입니다.

현재 버전: **4.0.0**

## 다운로드와 실행

1. [최신 버전 다운로드](https://github.com/lee3215-ko/site-flow-manager/releases/latest)에서 `SiteFlow-Windows-x64.zip`을 받습니다.
2. 쓰기 가능한 폴더(예: 바탕화면)에 전체 압축을 풉니다.
3. [Node.js LTS](https://nodejs.org/)를 설치합니다. Cloudflare 배포 명령 실행에 필요합니다.
4. `SiteFlow/SiteFlow.exe`를 실행합니다. Python 설치는 필요 없습니다.
5. Cloudflare 설정에서 본인 계정의 Account ID와 Pages Write API Token을 등록합니다.
6. `네이버 로그인 저장`에서 본인 네이버 계정으로 로그인합니다.

Windows 10/11 64비트용입니다. EXE와 browser 폴더는 함께 있어야 합니다.
다른 사용자에게는 위 배포 ZIP만 전달하세요. 로그인 상태나 API 토큰은 포함되지 않습니다.
실행 파일은 현재 코드 서명 인증서가 없으므로 Windows에서 게시자 확인 안내가 나타날 수 있습니다.

## 주요 기능

- ZIP 여러 개 등록, 홈페이지 타이틀 표시와 검색, 주소 복사, 목록 삭제
- 실제 Cloudflare 프로젝트 주소 확인 후 네이버 등록 및 소유확인 HTML 삽입
- 배포 후 확인 HTML, robots.txt, sitemap.xml, IndexNow 키 내용 검증
- 성공한 ZIP을 원래 폴더의 성공 폴더로 이동
- 사이트별 랜덤 배포 간격과 Cloudflare 여러 계정 관리
- 소유확인 후 수집 주기, robots.txt, 사이트맵, 페이지 수집 요청
- 소유확인을 진행한 네이버 아이디 수동 기록

소유확인 캡차는 사용자가 직접 진행합니다. 완료 후 사이트를 선택하고 소유확인 완료 표시를 누르세요.
페이지 수집 요청은 사이트별 최대 50개 URL을 처리합니다.
Cloudflare 한도에 관한 표시는 앱의 계정당 100개 기준이며 실제 적용 한도는 서비스 계정 정책에 따릅니다.
계정 자동 전환은 각 계정의 사용 권한과 서비스 정책을 준수하는 범위에서 사용하세요.

## 자동 업데이트

시작할 때 GitHub Releases의 최신 정식 버전을 자동으로 확인합니다.
새 버전이 있으면 현재/새 버전을 보여주고, 업데이트를 선택하면 다운로드, SHA-256 검증, 파일 교체, 재시작을 자동 진행합니다.
작업 중에는 업데이트 안내를 작업이 끝난 뒤 표시합니다.
왼쪽의 버전 확인 / 업데이트 버튼으로 직접 확인할 수도 있습니다.

네트워크 오류가 나면 기존 프로그램을 계속 사용할 수 있습니다. 파일 교체 실패 시 이전 파일을 복원합니다.
업데이트 전 파일은 설치 폴더 옆 .siteflow-update-* 폴더의 backup에 보관됩니다.
프로그램 데이터는 %LOCALAPPDATA%/CodexSitePublisher에 저장되어 업데이트해도 유지됩니다.
API 토큰은 Windows DPAPI로 암호화되며 네이버 로그인 상태 파일도 해당 PC에만 저장됩니다.

v3.8 이하 사용자는 이번 v4.0.0 ZIP을 한 번 직접 받아 실행해야 합니다. 이후 버전부터 자동 업데이트를 사용할 수 있습니다.

## 개발 및 새 버전 배포

Python 3.13, Windows 64비트에서:

```powershell
python -m pip install -r requirements-build.txt
python -m playwright install chromium
python -m unittest discover -s tests -v
python scripts/build_release.py
```

빌드 스크립트는 실행 파일, 설치된 Playwright의 Chromium, README, 버전 정보만 패키징합니다.
사이트 ZIP, 계정 설정, 로그인 프로필, 로그와 개발 테스트 데이터는 포함하지 않습니다.

새 버전을 배포하려면 version.py의 APP_VERSION과 이 문서의 버전을 올리고 변경을 커밋한 후 태그를 올립니다.

```powershell
git add app.py version.py README.md
git commit -m "Release 4.0.1"
git tag v4.0.1
git push origin main
git push origin v4.0.1
```

GitHub Actions가 테스트와 Windows 빌드 후 ZIP과 SHA256SUMS.txt를 Release에 게시합니다.
태그 번호는 version.py와 같아야 합니다. 소스 커밋만으로 사용자에게 업데이트가 전달되지는 않으며 새 Release가 필요합니다.
Release가 게시되면 사용자 프로그램이 다음 실행 또는 버전 확인 시 새 버전을 감지합니다.
