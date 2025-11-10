# PDF Vertical Tabs Viewer

Microsoft Edge의 세로 탭 UI에서 영감을 얻은 다중 PDF 뷰어입니다. 여러 문서를 한 창에 띄운 뒤 오른쪽 세로 탭 레일에서 빠르게 전환할 수 있으며, 공간이 좁을 때는 아이콘 전용 모드로 접을 수 있습니다.


## 주요 기능
- **세로 탭 레일**: 썸네일과 파일명을 함께 표시하고 드래그로 순서를 바꿀 수 있습니다.
- **컨텍스트 메뉴**: 탭에서 파일명 복사·다른 이름으로 저장·파일 위치 열기·닫기를 바로 실행할 수 있고, 뷰 영역에서는 `저장하기`, `다른 이름으로 저장하기`, 인쇄 미리보기, 회전, 확대/축소 등의 도구를 제공합니다.
- **파일 변경 감지**: 외부 편집기로 저장한 PDF를 자동으로 감지해 다시 불러오거나 상태를 최신으로 유지합니다.
- **문서 편집 도구**: 문서/페이지 단위 회전, 현재 변경분 저장, 다른 이름으로 저장, 페이지 이미지를 PNG/JPEG로 내보내기 기능을 제공합니다.
- **사용자 설정**: 컴팩트 탭 모드, 탭 패널 숨김, 기본 동작 등을 QSettings를 통해 기억합니다.

## 요구 사항
- Python 3.9 이상 (Windows, macOS, Linux에서 테스트)
- [PyQt5](https://pypi.org/project/PyQt5/)
- [PyMuPDF (fitz)](https://pypi.org/project/PyMuPDF/)

## 설치
```bash
git clone https://github.com/<your-account>/pdf_vertview.git
cd pdf_vertview
python -m venv .venv
.venv\Scripts\activate         # macOS/Linux: source .venv/bin/activate
pip install --upgrade pip
pip install PyQt5 PyMuPDF
```

## 실행
```bash
# 빈 뷰어 실행
python pdf_vertview.py

# 여러 PDF를 동시에 로드
python pdf_vertview.py report1.pdf invoice.pdf
```

## 사용 팁
- 세로 탭에서 우클릭하면 파일명 복사, 다른 이름으로 저장, 파일 위치 열기, 닫기를 바로 실행할 수 있습니다.
- 뷰 영역에서 우클릭하면 `저장하기`, `다른 이름으로 저장하기`, 인쇄 미리보기, 페이지/문서 회전, 확대·축소, 페이지 맞춤 등의 도구를 사용할 수 있습니다.
- 페이지 이미지를 내보내려면 `수정` 메뉴 또는 뷰어 컨텍스트 메뉴의 `이미지로 내보내기`를 사용하세요.
- 외부 에디터에서 파일을 수정하면 자동 감지 후 다시 불러올지 묻습니다.

## 패키징 및 배포
- **PyOxidizer**: `pyoxidizer.bzl` 구성을 이용해 독립 실행형 바이너리를 만들 수 있습니다. 예) `pyoxidizer build`.
- **Inno Setup**: `installer-pdf_vertview.iss` 및 `pdf_vertview_installer.iss` 스크립트로 Windows 설치 프로그램을 생성할 수 있습니다. 예) `iscc installer-pdf_vertview.iss`.
- **아이콘/릴리스 자료**는 `icon.ico`, `RELEASE_NOTES.md` 등을 참고하세요.

## 릴리스 노트
각 버전별 변경 사항은 [`RELEASE_NOTES.md`](RELEASE_NOTES.md)에서 확인할 수 있습니다. GitHub 릴리스와 동일한 정보를 제공합니다.
