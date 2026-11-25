# Build on the target OS: pyinstaller --clean --noconfirm 'Light Academia.spec'
import sys
import importlib.util
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

root = Path(SPECPATH)
datas = [(str(root / name), name if (root / name).is_dir() else ".") for name in (
    "app.py", "assets", "starter-notebook", "AGENT_PROMPT.md",
    "AGENT_PROMPT_ACTION.md", "AGENT_PROMPT_CHAT.md",
)]
datas += [(str(root / ".streamlit" / "config.toml"), ".streamlit")]
binaries = []
hiddenimports = ["app"]
for package in ("streamlit", "code_editor", "webview"):
    datas += collect_data_files(package)
hiddenimports += ["streamlit.web.cli", "streamlit.runtime.scriptrunner.magic_funcs"]
if sys.platform == "darwin":
    hiddenimports += ["webview.platforms.cocoa"]
elif sys.platform == "win32":
    hiddenimports += ["webview.platforms.winforms", "webview.platforms.edgechromium"]
else:
    hiddenimports += ["webview.platforms.gtk", "webview.platforms.qt"]
if importlib.util.find_spec("openai_codex"):
    codex_datas, codex_binaries, codex_imports = collect_all("openai_codex")
    datas += codex_datas + copy_metadata("openai-codex")
    binaries += codex_binaries
    hiddenimports += codex_imports
    # The SDK resolves its pinned executable and metadata from this separate package.
    runtime_datas, runtime_binaries, runtime_imports = collect_all("codex_cli_bin")
    datas += runtime_datas + copy_metadata("openai-codex-cli-bin")
    binaries += runtime_binaries
    hiddenimports += runtime_imports

datas += collect_data_files("streamlit_extras")
for distribution in ("streamlit", "streamlit-extras", "streamlit-code-editor"):
    datas += copy_metadata(distribution)

a = Analysis([str(root / "desktop_entry.py")], pathex=[str(root)],
             binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             excludes=["torch", "tensorflow", "jax", "keras", "transformers",
                       "matplotlib", "sklearn", "IPython", "notebook"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Light Academia",
          console=False, icon=str(root / "assets" / "logo.png"))
coll = COLLECT(exe, a.binaries, a.datas, name="Light Academia")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="Light Academia.app", icon=str(root / "assets" / "logo.png"),
                 bundle_identifier="org.lightacademia.desktop",
                 info_plist={"CFBundleName": "Light Academia",
                             "CFBundleDisplayName": "Light Academia",
                             "NSHighResolutionCapable": True})
