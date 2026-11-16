"""Local browser QA only: actual application with an in-process fake provider."""
import os
from pathlib import Path

if (os.environ.get('TRADE_PAPER_ENV') != 'test'
        or os.environ.get('TRADE_PAPER_ORDER_AI_API_KEY') != 'browser-offline-placeholder'):
    raise RuntimeError('Only isolated offline browser QA may load this entrypoint')

from app.main import app
from app import order_ai
from tests.test_order_ai import response_data


async def fake_generate(text):
    marker = Path(os.environ['TRADE_PAPER_DATA_DIR']) / 'fake_provider_calls.txt'
    with marker.open('a') as stream:
        stream.write('fictional call\n')
    return response_data()


order_ai._generate = fake_generate
