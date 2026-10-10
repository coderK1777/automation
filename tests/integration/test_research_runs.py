"""Research routes use isolated declared candles, never application history."""
from decimal import Decimal

from sqlalchemy import select


def test_pinned_research_ignores_later_operational_cache_revision(paper_fixture):
    from tradebot.models import Candle
    client, factory, identifier = paper_fixture
    dataset = client.post('/api/v1/market/datasets', json={'symbol':'BTCUSD','timeframe_seconds':60,'limit':80})
    assert dataset.status_code == 201
    payload = {'strategy_id':identifier,'kind':'COST_STRESS','dataset_id':dataset.json()['id'],
               'limit':80,'parameters':{'multipliers':[1]}}
    first = client.post('/api/v1/research/runs', json=payload)
    assert first.status_code == 201, first.text
    with factory() as db:
        latest = db.scalar(select(Candle).order_by(Candle.timestamp.desc()).limit(1))
        latest.close += Decimal('200')
        latest.high = latest.close+Decimal('100')
        db.commit()
    second = client.post('/api/v1/research/runs', json=payload)
    assert second.status_code == 201, second.text
    assert first.json()['result']['reproducibility_hash'] == second.json()['result']['reproducibility_hash']
    assert len(second.json()['result']['runs']) == 4
    assert client.get('/api/v1/research/runs/'+str(second.json()['id'])).status_code == 200


def test_research_rejects_missing_immutable_dataset_and_incomplete_history(paper_fixture):
    from tradebot.models import Candle
    client, factory, identifier = paper_fixture
    missing = client.post('/api/v1/research/runs', json={'strategy_id':identifier,'kind':'BENCHMARK','dataset_id':'a'*64,'limit':80})
    assert missing.status_code == 409
    with factory() as db:
        middle = db.scalar(select(Candle).order_by(Candle.timestamp).offset(30).limit(1))
        db.delete(middle)
        db.commit()
    incomplete = client.post('/api/v1/research/runs', json={'strategy_id':identifier,'kind':'OUT_OF_SAMPLE','limit':80})
    assert incomplete.status_code == 409
    assert 'missing' in incomplete.text.lower()


def test_portfolio_simulates_distinct_strategy_allocations(paper_fixture):
    client, _, identifier = paper_fixture
    config = client.get(f'/api/v1/strategies/{identifier}').json()['config']
    other = client.post('/api/v1/strategies', json={'name':'Second isolated research sleeve','config':config})
    assert other.status_code == 201
    other_id = other.json()['id']
    assert client.post(f'/api/v1/strategies/{other_id}/validate').status_code == 200
    response = client.post('/api/v1/research/runs', json={'strategy_id':identifier,'kind':'PORTFOLIO','initial_capital':'10000',
                         'limit':80,'parameters':{'symbols':['BTCUSD'],'strategy_ids':[identifier,other_id],
                         'allocations':{str(identifier):'0.4',str(other_id):'0.6'}}})
    assert response.status_code == 201, response.text
    result = response.json()['result']['simulation']
    assert Decimal(result['starting_capital']) == Decimal('10000')
    assert Decimal(result['allocation_fractions'][str(identifier)]) == Decimal('0.4')
    assert result['equity_curve']
    assert any('netting' in warning for warning in result['warnings'])
