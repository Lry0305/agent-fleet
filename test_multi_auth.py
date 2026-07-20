"""端到端测试：三种认证方式"""
import requests, json, sys
sys.path.insert(0, '.')

from agents.agentpay_sdk import AgentPayClient

API = 'http://127.0.0.1:8765'
passed = 0
failed = 0

def check(step, resp):
    global passed, failed
    if isinstance(resp, requests.Response):
        ok = resp.status_code < 400
        data = resp.json()
    else:
        ok = not resp.get('error')
        data = resp
    status = '✅' if ok else '❌'
    if ok: passed += 1
    else: failed += 1
    msg = json.dumps(data, ensure_ascii=False)[:250]
    print(f'  {status} {step}: {msg}')

def post(path, data=None):
    return requests.post(f'{API}{path}', json=data or {}, timeout=10)

def get(path, token=''):
    h = {'Authorization': f'Bearer {token}'} if token else {}
    return requests.get(f'{API}{path}', headers=h, timeout=10)

# ═══════ Test 1: API Key 注册 & 登录 ═══════
print('\n' + '='*60)
print('TEST 1: API Key 注册 & 登录')
print('='*60)

r = post('/api/agents/register', {
    'name': 'StockBot', 'role': 'provider', 'auth_method': 'api_key',
    'service_name': 'stock_analysis', 'service_description': '股票技术分析', 'price_per_call': 5,
})
check('Provider 注册', r)
api_key_provider = r.json().get('api_key','')

r = post('/api/agents/login', {'auth_method': 'api_key', 'api_key': api_key_provider})
check('Provider 登录', r)
provider_id = r.json().get('agent_id','')

r = post('/api/agents/register', {'name': 'TraderBot', 'role': 'consumer', 'auth_method': 'api_key'})
check('Consumer 注册', r)
api_key_consumer = r.json().get('api_key','')

r = post('/api/agents/login', {'auth_method': 'api_key', 'api_key': api_key_consumer})
check('Consumer 登录', r)
consumer_id = r.json().get('agent_id','')

check('Consumer 查余额', get('/api/billing/balance', api_key_consumer))

r = requests.post(f'{API}/api/billing/pay',
    headers={'Authorization': f'Bearer {api_key_consumer}', 'Content-Type': 'application/json'},
    json={'provider_id': provider_id, 'amount': 10}, timeout=10)
check('Consumer 支付', r)
tx_id = r.json().get('transaction_id','')

r = requests.post(f'{API}/api/billing/confirm/{tx_id}',
    headers={'Authorization': f'Bearer {api_key_consumer}', 'Content-Type': 'application/json'}, timeout=10)
check('Consumer 确认', r)

r = requests.post(f'{API}/api/openclaw/execute',
    headers={'Authorization': f'Bearer {api_key_provider}', 'Content-Type': 'application/json'},
    json={'task_type': 'data_query', 'target': '查AAPL股价', 'consumer_id': consumer_id, 'charge_amount': 5},
    timeout=10)
check('Provider 执行任务', r)

check('Provider 余额', get('/api/billing/balance', api_key_provider))

# ═══════ Test 2: 飞书 App ID ═══════
print('\n' + '='*60)
print('TEST 2: 飞书 App ID 注册 & 登录')
print('='*60)

r = post('/api/agents/register', {
    'name': 'FeishuStockBot', 'role': 'provider', 'auth_method': 'feishu_app',
    'app_id': 'cli_feishu_001', 'app_secret': 'feishu_secret_123',
    'service_name': 'stock_analysis', 'service_description': '飞书股票机器人', 'price_per_call': 3,
})
check('飞书 Provider 注册', r)

r = post('/api/agents/login', {
    'auth_method': 'feishu_app', 'app_id': 'cli_feishu_001', 'app_secret': 'feishu_secret_123',
})
check('飞书 Provider 登录', r)

# 错误密码
r = post('/api/agents/login', {
    'auth_method': 'feishu_app', 'app_id': 'cli_feishu_001', 'app_secret': 'wrong_secret',
})
if r.status_code >= 400:
    print('  ✅ 飞书错误密码被拒绝')
    passed += 1
else:
    print('  ❌ 飞书错误密码未拦截')
    failed += 1

# 注册飞书 Consumer
r = post('/api/agents/register', {
    'name': 'FeishuConsumer', 'role': 'consumer', 'auth_method': 'feishu_app',
    'app_id': 'cli_fs_consumer', 'app_secret': 'fs_consumer_secret',
})
check('飞书 Consumer 注册', r)
fs_api_key = r.json().get('api_key','')

# 跨方法：飞书注册的也能用 api_key 登录 (backup)
r = post('/api/agents/login', {'auth_method': 'api_key', 'api_key': fs_api_key})
check('飞书Agent 用APIKey登录(备用)', r)

# ═══════ Test 3: OpenClaw Bot ═══════
print('\n' + '='*60)
print('TEST 3: OpenClaw Bot 注册 & 登录')
print('='*60)

r = post('/api/agents/register', {
    'name': 'OCQueryBot', 'role': 'consumer', 'auth_method': 'openclaw_bot',
    'app_id': 'bot_token_oc_999',
})
check('OpenClaw Consumer 注册', r)

r = post('/api/agents/login', {
    'auth_method': 'openclaw_bot', 'bot_token': 'bot_token_oc_999',
})
check('OpenClaw Consumer 登录', r)

# 错误 token
r = post('/api/agents/login', {
    'auth_method': 'openclaw_bot', 'bot_token': 'wrong_token',
})
if r.status_code >= 400:
    print('  ✅ OpenClaw 错误Token被拒绝')
    passed += 1
else:
    print('  ❌ OpenClaw 错误Token未拦截')
    failed += 1

# ═══════ Test 4: Python SDK ═══════
print('\n' + '='*60)
print('TEST 4: Python SDK 多认证')
print('='*60)

# SDK register with feishu
client = AgentPayClient()
r = client.register('SDKFeishuBot', 'provider', auth_method='feishu_app',
    app_id='cli_sdk_002', app_secret='sdk_secret_456',
    service_name='translate', price_per_call=2)
check('SDK 飞书注册', r)

# SDK login with feishu
agent = client.login_with_feishu(app_id='cli_sdk_002', app_secret='sdk_secret_456')
print(f'  ✅ SDK 飞书登录: {agent.name} ({agent.role}) credits={agent.credit_balance}')
passed += 1

# SDK login with openclaw
client2 = AgentPayClient()
agent2 = client2.login_with_openclaw(bot_token='bot_token_oc_999')
print(f'  ✅ SDK OpenClaw登录: {agent2.name} ({agent2.role}) credits={agent2.credit_balance}')
passed += 1

# SDK login with api_key
client3 = AgentPayClient()
agent3 = client3.login(api_key=api_key_provider)
print(f'  ✅ SDK APIKey登录: {agent3.name} ({agent3.role}) credits={agent3.credit_balance}')
passed += 1

# SDK 飞书 agent 查看余额
bal = client.get_balance()
print(f'  ✅ SDK 飞书Agent查余额: {bal.get("credit_balance")} credits')
passed += 1

# SDK openclaw agent 查看市场
agents = client2.list_agents()
print(f'  ✅ SDK OpenClawAgent查市场: {len(agents)} agents online')
passed += 1

# ═══════ Test 5: 飞书 SDK Agent 发起支付 ═══════
print('\n' + '='*60)
print('TEST 5: 飞书 Agent 支付流程')
print('='*60)

# 飞书 agent (consumer角色) 向 StockBot 支付
client2_pay = AgentPayClient()
client2_pay.login_with_feishu(app_id='cli_fs_consumer', app_secret='fs_consumer_secret')
try:
    tx = client2_pay.pay(provider_id=provider_id, amount=5)
    print(f'  ✅ 飞书Consumer 支付成功: {tx.transaction_id} amount={tx.amount}')
    passed += 1
    client2_pay.confirm(tx.transaction_id)
    print(f'  ✅ 飞书Consumer 确认成功')
    passed += 1
except Exception as e:
    print(f'  ❌ 飞书Consumer 支付失败: {e}')
    failed += 1

# ═══════ Summary ═══════
print('\n' + '='*60)
print(f'🏁 测试结果: {passed} passed, {failed} failed  (共 {passed+failed} 项)')
print('='*60)
sys.exit(0 if failed == 0 else 1)
