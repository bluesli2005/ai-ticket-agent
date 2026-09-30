# 项目开发约定

## 前端源码可读性

- 所有维护的 JavaScript、CSS、HTML 及 HTML 内嵌脚本和样式保持展开、缩进和清晰命名，不进行压缩或混淆。
- CSS 选择器、属性和媒体查询使用分行缩进；HTML 标签与属性保持清楚的层级结构。
- 不手写压成一行的业务逻辑；优先使用清晰的函数与控制流程。
- 遵循 .prettierrc.json；格式化不应改变业务行为。SVG 路径等数据字符串可保持原样。
- 当前项目直接提供可读源码，不增加压缩构建步骤。

格式检查：`npm exec --yes --package=prettier@3.6.2 -- prettier --check "static/**/*.js" "static/**/*.css" "*.html"`
