# 项目开发约定

## JavaScript 可读性

- 所有维护的 JavaScript（包括 HTML 内嵌脚本）保持展开、缩进和清晰命名，不进行压缩或混淆。
- 不手写压成一行的业务逻辑；优先使用清晰的函数与控制流程。
- 遵循 .prettierrc.json；格式化不应改变业务行为。SVG 路径等数据字符串可保持原样。
- 当前项目直接提供可读源码，不增加压缩构建步骤。

格式检查：`npm exec --yes --package=prettier@3.6.2 -- prettier --check static/app.js`
