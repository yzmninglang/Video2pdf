# Video2PDF Batch Web (FastAPI)

该工程使用 **FastAPI + 原生前端 + 后台任务队列**，支持视频批量转 PDF，且任务不依赖网页连接。

## 核心能力

- 目录映射：在网页中输入或使用容器挂载目录作为根目录。
- 文件夹全列出：文件夹列表完整展示，可递归处理。
- 视频不全量展示：通过正则搜索返回结果，支持多个正则（逗号/分号/换行分隔）。
- 算法开关：`FrameDiff` 可单独使用；也可作为 `MOG2/KNN` 的可控二次确认开关。
- 方向开关：可自动检测竖屏素材；若为竖屏，PDF 按横向每页拼接 3 张（不足 3 张用最后一张补齐）。
- 后台任务：提交后由后台 Worker 执行，关闭浏览器不影响任务运行。
- 进度可见：网页可查看 Job 列表和 Job 详情（每个视频状态/进度/输出路径）。
- 任务归档：主页仅展示进行中任务（支持停止）；已完成任务归档到 `/history` 页面。
- 参数持久化：页面参数和搜索条件会自动保存到 `STATE_DIR/settings.json`。
- 输出规则：PDF 输出到视频同级目录 `pdf/` 子目录，文件名与视频同名。
  - 例如 `/data/course/a.mp4` -> `/data/course/pdf/a.pdf`
- 移动端可用：响应式页面，支持手机操作。

## 目录说明

- `engine.py`：抽帧、去重、PDF 核心逻辑。
- `job_manager.py`：任务队列、后台执行、状态持久化。
- `api_app.py`：FastAPI 后端。
- `web/`：前端静态页面（HTML/CSS/JS）。
- `Dockerfile` / `docker-compose.yml`：容器化部署。

## Docker 构建与运行

### 1) 推荐命令（兼容旧版 docker-compose）

```bash
cd /home/yzmin/OSRP/video2pdf
mkdir -p state_data

STATE_DIR=./state_data HOST_PORT=7861 \
HTTP_PROXY=http://hinas-v4.ninglang.top:7892/ \
HTTPS_PROXY=http://hinas-v4.ninglang.top:7892/ \
docker-compose up -d --build
```

访问：

```text
http://127.0.0.1:7861
```

默认映射：

- `./example -> /data`（视频目录）
- `./state_data -> /state`（任务状态与参数持久化）

说明：当前镜像默认以 `root` 运行，优先保证挂载目录在各种场景下可写。

### 2) 如果遇到 docker-compose v1 的 `ContainerConfig` 错误

```bash
docker-compose down --remove-orphans
docker rm -f $(docker ps -aq --filter "name=video2pdf-batch") 2>/dev/null || true
```

然后重试 `docker-compose up -d --build`。

### 3) 仅拉取镜像运行（不本地 build）

新增了 `docker-compose.image.yml`，用于直接拉取镜像。

```bash
cd /home/yzmin/OSRP/video2pdf
mkdir -p state_data

VIDEO2PDF_IMAGE=video2pdf-batch:latest \
HOST_PORT=7861 STATE_DIR=./state_data \
docker compose -f docker-compose.image.yml pull

VIDEO2PDF_IMAGE=video2pdf-batch:latest \
HOST_PORT=7861 STATE_DIR=./state_data \
docker compose -f docker-compose.image.yml up -d
```

如果你的镜像在远端仓库，例如：

```bash
VIDEO2PDF_IMAGE=ghcr.io/your-org/video2pdf-batch:latest \
docker compose -f docker-compose.image.yml up -d
```

## FastAPI 接口

- `GET /health`：健康检查。
- `GET /api/defaults`：默认映射目录与用户保存的参数。
- `POST /api/settings`：保存用户参数（自动调用）。
- `POST /api/scan`：扫描目录，仅返回文件夹列表和总视频计数。
- `POST /api/videos/search`：按正则搜索视频（支持多模式）。
- `POST /api/jobs`：提交后台任务。
- `POST /api/jobs/{job_id}/stop`：停止任务，并自动清理该任务当前视频的中间缓存（如生成图片）。
- `GET /api/jobs`：任务列表。
- `GET /api/jobs/{job_id}`：任务详情。

## 本地开发（非 Docker）

```bash
pip install -r requirements.txt
python api_app.py --mapped-dir ./example --state-dir ./state_data --server-name 0.0.0.0 --server-port 7860
```

浏览器访问：

```text
http://127.0.0.1:7860
```
