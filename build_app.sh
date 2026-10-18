#!/bin/bash
# build_app.sh — 生成 macOS 的 ktrans.app（本机自用版）
#
# 说明：这个 .app 是「外壳 + 启动脚本」结构，内部引用本机 Python 路径，
#       所以只适合自己用；要发给别人请等 py2app 全量打包版本。
#
# 用法：./build_app.sh    → 产出 dist/ktrans.app
set -e
cd "$(dirname "$0")"

VERSION="1.00"
APP="dist/ktrans.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

# 1. 探测带可用 tkinter 的 Python
#    注意：托管 venv 里的 python 缺 tcl9.0，tkinter 起不来，所以这里只试
#    托管基础解释器和系统/brew 解释器。
CANDIDATES=(
  "$HOME/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
  "/opt/homebrew/bin/python3"
  "/usr/bin/python3"
  "$HOME/anaconda3/bin/python3"
)

PY=""
for P in "${CANDIDATES[@]}"; do
  if [ -x "$P" ] && "$P" -c "import tkinter as t; r=t.Tk(); r.withdraw(); r.destroy()" >/dev/null 2>&1; then
    PY="$P"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "✗ 找不到带可用 tkinter 的 Python，无法打包"
  exit 1
fi
echo "✓ 使用解释器：$PY"

# 2. 找 tkinterdnd2 所在的 site-packages（拖拽库），挂进 PYTHONPATH
SITE=$("$PY" -c "import tkinterdnd2, os; print(os.path.dirname(os.path.dirname(tkinterdnd2.__file__)))" 2>/dev/null || true)
if [ -z "$SITE" ]; then
  for C in "$HOME/.workbuddy/binaries/python/envs/default/lib/python3.13/site-packages"; do
    [ -d "$C" ] && SITE="$C" && break
  done
fi
echo "✓ 拖拽库路径：${SITE:-（未找到，GUI 将不支持拖放）}"

# 3. Info.plist
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleName</key>
	<string>ktrans</string>
	<key>CFBundleDisplayName</key>
	<string>ktrans</string>
	<key>CFBundleIdentifier</key>
	<string>com.kliocp.ktrans</string>
	<key>CFBundleVersion</key>
	<string>1</string>
	<key>CFBundleShortVersionString</key>
	<string>$VERSION</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleExecutable</key>
	<string>ktrans</string>
	<key>CFBundleInfoDictionaryVersion</key>
	<string>6.0</string>
	<key>LSMinimumSystemVersion</key>
	<string>12.0</string>
	<key>NSHighResolutionCapable</key>
	<true/>
	<key>LSApplicationCategoryType</key>
	<string>public.app-category.utilities</string>
	<key>CFBundleDocumentTypes</key>
	<array>
		<dict>
			<key>CFBundleTypeName</key>
			<string>Audio</string>
			<key>CFBundleTypeRole</key>
			<string>Viewer</string>
			<key>LSItemContentTypes</key>
			<array>
				<string>public.audio</string>
				<string>public.folder</string>
			</array>
		</dict>
	</array>
</dict>
</plist>
PLIST

# 4. 启动脚本
cat > "$APP/Contents/MacOS/ktrans" <<LAUNCH
#!/bin/bash
# ktrans $VERSION 启动器（由 build_app.sh 生成，不要手改）
PROJ="$PWD"
PY="$PY"
export PYTHONPATH="$SITE\${PYTHONPATH:+:\$PYTHONPATH}"

# 运行日志：排查"双击没反应"时看这里
LOG="/tmp/ktrans.log"
echo "==== \$(date +%F_%T) 启动 ktrans, argv: \$* ====" >> "\$LOG"

# 单实例保护：PID 锁文件（exec 后 python 沿用同一 PID；
# 锁里的进程已死则视为陈旧锁，照常启动）
LOCK="/tmp/ktrans.pid"
if [ -f "\$LOCK" ]; then
    OLD=\$(cat "\$LOCK" 2>/dev/null)
    if [ -n "\$OLD" ] && kill -0 "\$OLD" 2>/dev/null; then
        echo "==== 已有实例在运行 (pid=\$OLD)，跳过 ====" >> "\$LOG"
        exit 0
    fi
    echo "==== 清理陈旧锁 (pid=\$OLD) ====" >> "\$LOG"
fi
echo \$\$ > "\$LOCK"

exec "\$PY" "\$PROJ/mp3_to_mp4_gui.py" "\$@" >> "\$LOG" 2>&1
LAUNCH

chmod +x "$APP/Contents/MacOS/ktrans"
touch "$APP"

# 5. 校验
if plutil -lint "$APP/Contents/Info.plist" >/dev/null 2>&1; then
  echo "✓ Info.plist 校验通过"
else
  echo "✗ Info.plist 有问题"; exit 1
fi

echo ""
echo "✓ 已生成 $APP"
echo "  双击即可运行，或拖到「应用程序」文件夹。"
echo "  日志：/tmp/ktrans.log"
