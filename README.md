# travel-cli

面向终端和个人 Bot 的旅行规划工具：比较出发时间的沿途天气、评估景点实际绕行、安排有时间预算的往返小旅行，并生成行程卡片与私有变化检查。Python 3.10+，只用标准库。

## 运行

```sh
git clone https://github.com/scieszk/travel-cli.git
cd travel-cli
python travel.py points
python travel.py weather 成都 --days 4
python travel.py route 北京 上海 --depart 08:30 --step 40
python travel.py route 北京 上海 --via 济南 --stop 济南=45 --structured
python travel.py compare 北京 上海 --departures 07:00 10:00 --structured
```

日期默认北京时间今天，可用 `--date YYYY-MM-DD` 指定今天至未来第15天；整个行程仍须在实际预报覆盖内。时刻使用严格的 `HH:MM`。各段使用地图参考时长；`--speed 80` 覆盖为每小时80公里，默认0使用地图值。`--step` 为采样间隔公里，最多200点。途经点按顺序行驶，`--stop 地点=分钟` 只接受已列出的途经点，停车会推迟后续到达。

天气查询示例城市无需地图密钥。地理编码、路线、景点需要自己的高德 **Web服务** 密钥。

## 景点绕行和限时小旅行

```sh
python travel.py route 成都 乐山 --sights --max-detour 30 --visit-minutes 60
python travel.py outing 成都 --hours 6 --interests nature --structured
python travel.py outing 成都 --depart 09:00 --return-by 18:00 --visit-minutes 120
python travel.py points --sights
python travel.py route 成都 成都大熊猫繁育研究基地 --structured
```

沿途最多查询6个样点，每点只取第一页20条景点/博物馆，默认半径3公里，`--radius` 最多5公里。**这不是整条路线的完整搜索**；查询中心附近距离不等于驾车绕行。最多对前5个候选重算经过景点及原有途经点的路线，展示额外总时间最短的3处。`--max-detour` 只限制额外驾驶，`total_extra_minutes` 另加预计游玩。重算时间比基准还短时列为无法可靠估算，不当作零绕行。候选不会自动加入原行程。

`outing` 在起点附近默认20公里、最多50公里范围寻找候选，最多计算5处的双向驾车路线，按总用时展示最多3套。只保留「往返驾驶＋游玩＋预留」不超过预算的方案。`--return-by` 是同日时刻，跨日用 `--hours`（最多24）。结果包含失败、未评估数量和覆盖说明。地图参考时间无法预测未来拥堵；营业、预约、停车、入园步行另行确认。

内置4处公共景点地址保留官方出处与核对日期，使用时才地理编码；地址未必是车辆入口。分类来自POI类型或精确匹配的内置记录，未知归为 `any`。`nature/culture` 是粗分类，不会编造评分、票价和开放信息。内置游玩时间为人工估计，实际停留以偏好或 `--visit-minutes` 为准。

## 私有偏好、卡片和变化检查

```sh
python travel.py preferences set max_detour_minutes=20 visit_minutes=90 interests=culture
python travel.py preferences set avoid_night_driving=true
python travel.py preferences show --structured
python travel.py preferences reset
python travel.py route 北京 济南 --allow-night-driving --card
python travel.py compare 北京 济南 --departures 07:00 10:00 --card --select 2
python travel.py outing 成都 --hours 6 --save-watch .private/weekend.watch.json --structured
python travel.py watch check .private/weekend.watch.json --structured
```

偏好默认保存于 `~/.config/travel-cli/preferences.json`，可用 `TRAVEL_PREFERENCES` 改路径。字段：`max_detour_minutes`（默认30，0–240）、`visit_minutes`（60，0–1440）、`buffer_minutes`（30，0–240）、`interests`（any/nature/culture）、`avoid_night_driving`（false）。显式参数优先；`--allow-night-driving` 覆盖避免夜间偏好。

夜间判断使用每个采样位置当天的日出/日落预报，同时检查停车后的离开时刻。它描述采样时刻，不证明点与点之间全程白天；缺失数据会说明未知。比较出发时间时先剔除明确不满足偏好的方案，再比较红色、黄色天气提示点数，并列保留；任何方案天气不完整或所需偏好判断未知时不自动推荐。

`--card` 输出可复制的 Markdown，包含预计时间、停留、天气和高德地图链接，不能同时用JSON模式。`--select` 从1开始，默认第一套。分享前自行检查个人行程。

`--save-watch FILE` 显式保存所选方案的最小基准、有效日期、路线参数和解析后的GCJ-02坐标，文件自动补 `.watch.json`。`watch check` 只检查一次，不创建定时任务、不发送消息。比较天气灯、夜间采样变化和至少30分钟的预计到达变动。数据不完整/无法重查时为 `state: unavailable` 且退出码非零；有效比较为 `changed/unchanged`。`notify` 表示本次有新的变化、不可用状态或恢复可用，相同结果连续检查不会重复通知。原始基准不变，重新规划并显式保存才替换。

路线形状或采样身份变化时无法逐点比较，报告不可用；基准本身不完整时需重新确认并保存。不要并发检查同一文件，本地去重文件不是多进程调度器。Bot可在具体行程确定后调用检查并依据 `state/notify` 提醒。

Unix新文件请求权限600、新建目录700；Windows依赖个人目录ACL。私有偏好、检查文件、密钥、个人点位均被Git忽略，提交检查也拒绝强制暂存这些文件。

## 密钥和坐标

密钥按优先级读取：环境变量 `AMAP_KEY`、`AMAP_KEY_FILE` 指定文件、工具目录 `.secrets/amap.key`（一行）。Linux建议目录700、文件600。`.env.example` 只列变量名，程序不自动加载 `.env`。

个人点位保存为 `waypoints.json`，参考 `waypoints.example.json`；或用 `TRAVEL_WAYPOINTS` 指定。未配置时使用公共城市。坐标支持 `coord_system: "gcj02"` 或 `"wgs84"`；旧文件省略时沿用GCJ-02。地图使用GCJ-02，天气请求前转为WGS84。转换为近似算法，不用于测绘。海拔为0仍传给天气接口，缺失时由天气接口估计，不借用附近景点海拔。

## Bot响应协议

所有命令支持 `--structured`（别名 `--json`），stdout只有一个JSON对象。命令错误返回非零退出码和 `ok:false`，`error` 含 `code/message`；成功结果在 `data`。公共字段有 `schema_version: "1.0"`、`command`、`queried_at`、`sources`。`queried_at` 为响应生成时间。

缺失天气字段为JSON `null`；各点 `weather_status` 为 available/incomplete/unavailable。景点/小旅行的 `complete` 仅表示已尝试的数据是否完整，不表示穷尽所有可能。`watch` 的不可用状态仍在 `data` 中，此时 `ok:true` 表示检查产生了结果，调用方须同时检查 `data.state` 和退出码；无法读取基准等错误则为 `ok:false`。错误不带密钥、URL查询参数或上游原始响应。

## 数据与许可

- 天气来自 [Open-Meteo](https://open-meteo.com/en/docs)，数据采用 [CC BY 4.0](https://open-meteo.com/en/licence)。终端、卡片和JSON保留署名。免费托管API受其[服务条款](https://open-meteo.com/en/terms)限制，代码MIT许可不改变服务条款。
- 地点/路线来自[高德 Web服务](https://lbs.amap.com/api/webservice/summary)，遵守配额与条款。同一进程的高德请求至少间隔1.05秒，短时QPS限流有有限重试；景点发现保持每中心一次请求。多个进程/应用共享密钥时仍可能限流，避免同时批量查询。错误码参见[官方说明](https://lbs.amap.com/api/webservice/guide/tools/info)。
- 风险灯是低温、降水、降雪等规则提示，不是通行、安全或概率结论。⚪为数据不足。到达时刻按各段折线距离比例插值，没有各小段实时速度；天气预报不是道路实况。
- 本项目采用MIT许可，第三方算法与出处见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 开发验证

```sh
python -m unittest discover -s tests -v
git config core.hooksPath .githooks
python scripts/check_private.py --all
python scripts/check_private.py --history
```

测试使用合成路线、天气、景点和临时私有文件，不需要真实密钥。提交钩子扫描暂存区，推送与CI扫描可达历史，包含后来删除的文件。检查只覆盖常见泄漏模式，仍需人工审查 `git diff --cached`，不要强制提交私有文件。
