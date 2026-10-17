# travel-cli

用 Python 标准库查询地点天气，或按驾车路线估算沿途到达时刻并匹配小时预报。适合终端和个人 Bot 调用。

## 运行

需要 Python 3.10+，不需要安装第三方依赖。

```sh
python travel.py points
python travel.py weather 成都 --days 4
python travel.py route 北京 上海 --depart 08:30 --step 40
python travel.py route 北京 上海 --via 济南 --speed 80
```

`--depart` 指北京时间当天的出发时刻。`--speed 0` 使用路线接口估算的均速，正数指定均速；`--step` 必须大于零，单位公里。天气预报支持 1–16 天；沿途小时预报只匹配实际返回的时间范围，超出范围会明确报错。

`weather` 查询示例点位不需要地图密钥。路线规划和未收录地点的地理编码需要自己的高德 Web 服务密钥。

## 私有配置

代码不包含密钥，也不会从 Git 获取密钥。任选一种配置方式：

- 设置环境变量 `AMAP_KEY`。
- 将密钥保存在工具目录的 `.secrets/amap.key`，仅一行；Linux 上将目录权限设为 `700`、文件权限设为 `600`。
- 设置 `AMAP_KEY_FILE`，指向其他私有文件。

环境变量优先于文件。`.env.example` 只记录变量名，程序不会自动加载 `.env`。

个人点位保存为 `waypoints.json`，格式参考 `waypoints.example.json`；或用 `TRAVEL_WAYPOINTS` 指定文件。未配置时使用示例城市。密钥文件、个人点位和输出文件均不进入 Git。

## 数据与限制

- 预报来自 [Open-Meteo](https://open-meteo.com/en/docs)，地点和路线来自[高德 Web 服务](https://lbs.amap.com/api/webservice/summary)。遵守各服务当前的使用规则与配额。
- 已知海拔会传给预报接口，包括海拔为 0 的地点；缺失数值显示“未知”，不会自动当成晴天或零降雨。
- 路线 ETA 使用均速近似，未计入休息和实时拥堵；路线折线采样不是完整的实时导航。
- 风险灯只是根据低温、降水、降雪的简单提示，不能据此确认道路开放或车辆能够安全通过。⚪ 表示数据不足；实际出行以现场情况和官方预警为准。
- 网络请求有超时和有限重试。错误只报告服务主机、路径和错误类型，不输出查询参数或密钥。

## 验证与提交

```sh
python -m unittest discover -s tests -v
git config core.hooksPath .githooks
python scripts/check_private.py --all
```

提交钩子扫描暂存区，推送钩子和 CI 还会扫描可达提交历史，包含后来删除的文件。这些检查只能发现常见泄漏模式，仍需人工检查 `git diff --cached`，不要使用 `git add -f` 将私有文件强行加入版本库。

仓库仅包含可复用代码、公开示例和测试；历史行程、聊天内容、饮食/体重记录没有导入。
