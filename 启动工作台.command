#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print '缺少项目虚拟环境。请按 README 的首次安装步骤安装。'
  read '?按回车退出'
  exit 1
fi
.venv/bin/python app.py
status_code=$?
if [[ $status_code != 0 ]]; then
  print '工作台未能启动，请查看上方提示。若已有实例运行，请直接打开 http://127.0.0.1:8765。'
  read '?按回车退出'
fi
exit $status_code
