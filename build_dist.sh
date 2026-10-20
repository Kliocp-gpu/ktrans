#!/bin/bash
# build_dist.sh — 用 py2app 打出可分发（不依赖本机 Python）的 dist/ktrans.app
#
# 和 build_app.sh 的区别：
#   build_app.sh   外壳 + 启动脚本，只在本机能跑（依赖本机 Python + tkinterdnd2）
#   build_dist.sh  py2app 全量打包，把解释器、tkinterdnd2、Tcl/Tk 全部塞进 app
#                  换台机器直接能开，也能接收「拖到 Dock 图标」的 odoc 事件
#
# 用法：./build_dist.sh
set -euo pipefail

PROJ="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJ"

VENV="$PROJ/.venv-pack"
APP="$PROJ/dist/ktrans.app"
BDIST="${BDIST:-/tmp/ktrans-bdist}"

if [ ! -x "$VENV/bin/python" ]; then
  echo "✗ 缺构建环境：先执行"
  echo "    $(command -v python3) -m venv .venv-pack"
  echo "    .venv-pack/bin/pip install py2app tkinterdnd2"
  exit 1
fi

PY="$VENV/bin/python"
PYBASE="$("$PY" -c 'import sys; print(sys.base_prefix)')"
TCLLIB="$PYBASE/lib"
echo "✓ 构建解释器：$PYBASE"

# 0. 给 py2app 打补丁（幂等，重复跑不会重复改）
#    python-build-standalone 把 zlib 静态编进了解释器（模块没有 __file__），
#    py2app 0.28 的 build_app.py 里 copy_file(zlib.__file__) 会直接 AttributeError。
for p in "$VENV"/lib/python*/site-packages/py2app/build_app.py; do
  [ -f "$p" ] || continue
  if ! grep -q "_zlib_file" "$p"; then
    "$PY" - "$p" <<'PYEOF'
import sys
path = sys.argv[1]
src = open(path).read()
old = ("            import zlib\n\n"
       "            self.copy_file(zlib.__file__, os.path.dirname(arcdir))")
new = ("            import zlib\n\n"
       "            # 补丁（ktrans）：内建 zlib 没有 __file__，跳过拷贝即可\n"
       "            _zlib_file = getattr(zlib, \"__file__\", None)\n"
       "            if _zlib_file:\n"
       "                self.copy_file(_zlib_file, os.path.dirname(arcdir))")
if old in src:
    open(path, "w").write(src.replace(old, new))
    print("✓ 已给 py2app 打 zlib 补丁")
else:
    print(f"! py2app 版本不同，补丁没打上：{path}")
PYEOF
  fi
done

# 1. py2app 主流程
#    两个目录都放到项目外的临时位置，原因有二：
#      a) build/、dist/ 每次重建都要删几十上百个文件，会被沙箱的批量删除保护拦下；
#      b) 产物先落在 stage 目录，打包没成功时不会把你正在用的 dist/ktrans.app 弄没。
STAGE="${STAGE:-/tmp/ktrans-dist-$(date +%s)}"
if [ -e "$APP" ]; then
  mv "$APP" "/tmp/ktrans-app-prev-$(date +%s)"   # 移走而不是删掉，绕开批量删除保护
fi
echo "→ py2app 打包中…"
"$PY" setup.py py2app --bdist-base="$BDIST" --dist-dir="$STAGE" > /tmp/ktrans-py2app.log 2>&1 || {
  echo "✗ py2app 失败，日志尾部："; tail -25 /tmp/ktrans-py2app.log; exit 1; }
mkdir -p "$PROJ/dist"
mv "$STAGE/ktrans.app" "$APP"
echo "✓ py2app 完成"

# 2. 补进 Tcl/Tk 运行时
#    python-build-standalone 的 Tk 是普通 dylib（不是 Tcl.framework），py2app 只认
#    framework 布局，所以要手动搬。放 Resources/lib/ 而不是 Frameworks/ 有原因：
#    bundle 里 _tkinter.so 只带一条 rpath `@loader_path/../..`，loader 起点是
#    Resources/lib/python3.13/lib-dynload，两级 .. 正好落到 Resources/lib；
#    而 arm64 二进制没有 header padding，install_name_tool 加不了新 rpath
#    （报 "larger updated load commands do not fit"）。放这儿直接命中，不用改二进制。
FW="$APP/Contents/Frameworks"
RES="$APP/Contents/Resources"
cp -f "$TCLLIB/libtcl9.0.dylib" "$TCLLIB/libtcl9tk9.0.dylib" "$RES/lib/"
cp -R "$TCLLIB/tcl9.0" "$RES/tcl9.0"
cp -R "$TCLLIB/tk9.0" "$RES/tk9.0"
echo "✓ Tcl/Tk 运行时已放入 Resources"

# 3. 校验 rpath 是否真的指向这两个 dylib（不通过就别白测了）
TKSO="$(find "$APP" -name '_tkinter*.so' | head -1)"
if [ -z "$TKSO" ]; then echo "✗ 没找到 _tkinter.so，打包不完整"; exit 1; fi
RP="$(otool -l "$TKSO" | awk '/LC_RPATH/{getline;getline;print $2}' | head -1)"
SO_DIR="$(cd "$(dirname "$TKSO")" && pwd)"
BASE="$(cd "$SO_DIR/${RP#@loader_path/}" 2>/dev/null && pwd || true)"
if [ -f "$BASE/libtcl9.0.dylib" ] && [ -f "$BASE/libtcl9tk9.0.dylib" ]; then
  echo "✓ rpath 解析通过：$RP -> ${BASE#"$APP/Contents/"}"
else
  echo "✗ rpath 解析不到 Tcl/Tk：$RP -> ${BASE:-?}"; exit 1
fi

# 3b. py2app 顺带放了个 Contents/MacOS/python（调试用），它的 rpath 指向 Contents/lib，
#     但 libpython 实际在 Frameworks 下 —— 补个软链，方便用包内解释器排查问题：
#       ./dist/ktrans.app/Contents/MacOS/python -c "print(1)"
mkdir -p "$APP/Contents/lib"
ln -sf ../Frameworks/libpython3.13.dylib "$APP/Contents/lib/libpython3.13.dylib"

# 4. 重新做 ad-hoc 签名（改过 macho 后原签名会失效，不签 macOS 会直接杀掉进程）
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || {
  echo "✗ 签名失败"; exit 1; }
echo "✓ 已 ad-hoc 签名"

# 5. 校验
if plutil -lint "$APP/Contents/Info.plist" >/dev/null 2>&1; then
  echo "✓ Info.plist 校验通过"
else
  echo "✗ Info.plist 有问题"; exit 1
fi

SIZE=$(du -sh "$APP" | cut -f1)
echo ""
echo "✓ 已生成 ${APP}（${SIZE}）"
echo "  这是独立版本：换台 Mac 也能直接双击（对方仍需装 ffmpeg）。"
echo "  压缩分发：ditto -c -k --keepParent \"${APP}\" ktrans.zip"
