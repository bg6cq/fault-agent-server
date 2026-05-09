# Linux 服务器故障状态收集

本系统分3部分：

1. fault-agent Linux端自查和上报故障

https://github.com/bg6cq/fault-agent

在每台Linux 机器中运行。

2. fault-agent-server 故障状态收集服务

接收 fault-agent 上报的故障信息，存放在 SQLite 数据库中。

3. fault-agent-web 故障状态显示服务

提供一个WEB界面，查看上报的故障信息。


# 我的使用

找一台Linux主机， 启动服务端：
```
cd /usr/src/

clone https://github.com/bg6cq/fault-agent-server

mkdir /var/lib/fault-agent-server/

python3 fault-agent-server/server.py --port 9001 --db /var/lib/fault-agent-server/reports.db  &
python3 fault-agent-web/web.py --port 9002 --db /var/lib/fault-agent-server/reports.db &
```

按照上述命令行，收集服务在9001端口，WEB界面在9002端口。

9002端口请做限制仅仅允许自己访问。


每台服务器上，/usr/src 目录下，测试运行，查看输出的是否正确

支持 JSON 和 YAML 两种配置格式，选择其一即可：

**JSON 格式：**
```
cp config.json.sample config.json
vi config.json # 修改 hostname、sysinfo、url 
python /usr/src/fault-agent/fault-agent.py --config /usr/src/fault-agent/config.json --oneshot
```

**YAML 格式：**
```
cp config.yaml.sample config.yaml
vi config.yaml # 修改 hostname、sysinfo、url
python /usr/src/fault-agent/fault-agent.py --config /usr/src/fault-agent/config.yaml --oneshot
```
如果正常
```
crontab -e 改为定期运行
```



