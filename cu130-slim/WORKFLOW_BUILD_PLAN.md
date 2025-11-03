# 工作流驱动的 ComfyUI 镜像改造方案

## 背景与目标

- 当前镜像在 `preload-cache.sh` 中固定拉取 ComfyUI 与一组常见插件，并通过 `pak3.txt`/`pak5.txt`/`pak7.txt` 安装相应依赖。
- 需求是基于指定工作流，按需解析所需插件，自动拉取对应仓库并安装插件依赖，从而生成定制化镜像。
- 已存在的 `builder-scripts/generate_workflow_dependencies.py` 可以输出工作流所需插件清单，需要围绕其结果扩展 Docker 构建流程。

## 总体策略

1. 保留 `pak3.txt`（基础通用依赖）与 `pak7.txt`（特殊 git 依赖），废弃全局性的 `pak5.txt`，改为动态生成 `workflow-requirements.txt`。
2. 在 Docker 构建阶段新增工作流参数与依赖解析逻辑，实现“有工作流则按需打包，无工作流时保持现有默认镜像”的兼容。
3. 增强 `preload-cache.sh`，根据解析结果克隆所需插件、汇总依赖、输出构建摘要。
4. 更新文档，指导用户如何以工作流驱动构建镜像。

## 实施步骤

1. **清理 pak5 相关内容**
   - 从 `Dockerfile` 中移除 `pip install -r /builder-scripts/pak5.txt`。
   - 从代码库中删除 `builder-scripts/pak5.txt` 和 `builder-scripts/generate-pak5.sh`，或将其标记为废弃以免混淆。

2. **Dockerfile 改造**
   - 新增 `ARG WORKFLOW_JSON`，并在存在该参数时 `COPY` 工作流文件到镜像临时目录。
   - 在 `RUN` 指令中调用 `generate_workflow_dependencies.py` 生成 `/tmp/workflow-deps.json`。
   - 将生成文件传递给 `preload-cache.sh`（环境变量或命令行参数）。
   - 在 `pip install` 阶段追加条件步骤：若 `/builder-scripts/workflow-requirements.txt` 存在，则执行 `pip install -r`。
   - 确保未传入工作流时跳过新增步骤，维持原有行为。

3. **`preload-cache.sh` 增强**
   - 接受工作流依赖文件路径（优先读取环境变量或脚本参数）。
   - 若存在依赖文件：
     - 解析 `plugins[*]`，确定 Git 仓库 URL（优先 `metadata.repo` / `metadata.github`，否则使用 `id`）。
     - 生成插件目录名（例如取仓库名或 slug 化处理）并克隆至 `/default-comfyui-bundle/ComfyUI/custom_nodes/<插件目录>`。
     - 收集插件内的 `requirements*.txt` / `requirements`，统一整理。
     - 对于未解析的节点，记录并在构建日志中高亮警告。
   - 汇总依赖：加载 `pak3.txt` 与 `pak7.txt`（规范化包名/URL）为基准集合，将新增依赖写入 `/builder-scripts/workflow-requirements.txt`，避免重复。
   - 生成构建摘要 `/builder-scripts/workflow-summary.json`，包含克隆的插件、未解析节点等信息，并打印到标准输出。

4. **依赖归并辅助逻辑**
   - 若 Shell 脚本处理复杂，可在 `builder-scripts` 中新增一个 Python 辅助脚本/函数，专门负责：
     - 读取 `pak3.txt`/`pak7.txt` 与收集到的插件 requirements。
     - 规范化包名（忽略版本、大小写、替换 `_`/`-`）。
     - 过滤掉已存在的包或 git 依赖，输出到 `workflow-requirements.txt`。
   - 在 `preload-cache.sh` 中调用该脚本确保逻辑清晰。

5. **文档更新**
   - 在 `README.adoc` 添加“基于工作流定制镜像”章节，说明：
     1. 导出工作流 JSON。
     2. 构建命令示例：`docker build --build-arg WORKFLOW_JSON=path/to/workflow.json -t comfyui-custom .`
     3. 构建结果中如何查看 `workflow-summary.json`。
     4. 未提供工作流时保持默认镜像。
   - 简要说明 `pak3`/`pak7` 的角色，以及 `workflow-requirements.txt` 的生成策略。

6. **验证与清理**
   - 本地运行一次 `docker build`（可使用简化或 mock 工作流）验证流程与日志输出。
   - 检查镜像中 `custom_nodes` 目录与 `workflow-summary.json`。
   - 确认默认路径（无工作流）仍可成功构建。
   - 清理临时文件，确认 `workflow-requirements.txt` 仅在需要时生成。

## 后续扩展（可选）

- 支持同时传递多个工作流文件并合并依赖。
- 为插件克隆添加缓存或镜像代理设置。
- 引入单元/集成测试以验证依赖解析逻辑。

以上步骤执行完毕后，代码库即可支持基于工作流定制的 ComfyUI 镜像构建流程。
