"""Opt-in, clearly labelled example IT tickets and knowledge. Idempotent."""
from pathlib import Path
from workspace import js,now


def seed(ws):
    with ws.lock,ws.connect() as con:
        if con.execute("SELECT 1 FROM settings WHERE key='seeded'").fetchone():
            return {'created':False,'message':'示例数据已经导入，不会重复创建'}
        con.execute("INSERT INTO settings VALUES ('seeded','1')")
    jobs=[]
    for p in sorted((Path(__file__).parent/'sample-docs').glob('*.md')):
        doc=ws.save_document({'title':p.stem,'filename':p.name,'content':p.read_text(),'sample':True})
        jobs.append(doc['job_id'])
    rows=[
      {'title':'VPN 连接失败，提示认证超时','description':'今天早上在家办公，VPN 一直提示认证超时，无法访问内部系统。网页可以正常打开，昨天还能连接。设备为 Mac。','category':'系统故障','priority':'高','requester':'陈晓 · 研发'},
      {'title':'更换手机后无法完成双重验证','description':'换了新手机，登录内部账号需要验证码，但旧手机已无法使用。希望恢复账号访问。','category':'账号登录','priority':'普通','requester':'林悦 · 设计'},
      {'title':'浏览器打开工作台后白屏','description':'更新浏览器后，工作台页面一直白屏。其他网站正常，同事可以正常访问。','category':'系统故障','priority':'普通','requester':'周宁 · 运营'},
      {'title':'希望工单支持批量导出','description':'每周需要汇总已解决的工单，希望能够按时间范围导出。','category':'功能需求','priority':'低','requester':'许安 · IT 支持'},
    ]
    for i,r in enumerate(rows):
        t=ws.create_ticket({**r,'sample':True})
        if i==2:
            ws.update_ticket(t['id'],{'version':t['version'],'status':'已解决','resolution':'确认仅当前浏览器受影响。在无痕窗口验证可访问后，关闭导致冲突的扩展，刷新页面恢复。未清除用户业务数据。'})
        if i==1: ws.update_ticket(t['id'],{'version':t['version'],'status':'处理中'})
    return {'created':True,'message':'已导入4条示例工单和知识文档，示例资料仅供体验','job_ids':jobs}
