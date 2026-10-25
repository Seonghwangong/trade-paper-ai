"""Explicit customer-sales switch; does not grant paid access or create charges."""
import os


def enabled():
    return all(os.environ.get('TRADE_PAPER_PADDLE_LIVE_' + flag) == '1'
               for flag in ('PUBLIC_CHECKOUT', 'CHECKOUT', 'WEBHOOK', 'ACCESS', 'MANAGE', 'CANCEL'))


NOTICE = ('Starter monthly checkout is available through Paddle. Review the final total and '
          'monthly renewal terms before paying. Professional remains contact-based.')


def customer_copy(source):
    """Update current availability copy at its rendering boundary, never saved records.

    Financial terms, refund rights and historical release entries are preserved.
    The switch defaults off, so deploying this code does not open customer sales.
    """
    if not enabled():
        return source
    replacements = {
        'Online payment is being prepared. Starter and Professional cannot be activated yet.': NOTICE,
        'No. Starter is offered at ₩29,000 per month, but online payment processing is not active yet. Apply for Founding Beta to discuss onboarding. Professional remains contact-based.': NOTICE,
        'Starter is available at a published monthly price. Online payment processing is not active yet.': NOTICE,
        'Online payment processing is not active yet. Viewing or applying for Starter does not create a paid subscription or collect payment.': NOTICE,
        'No online payment is collected while checkout is inactive. Review how future Starter cancellation and refund requests will be handled before applying.': 'Review the monthly renewal, cancellation and refund terms before paying. Viewing this page or applying for Founding Beta does not charge you.',
        'Online paid checkout is not active; this application does not create an account or charge you.': 'Starter checkout is available separately through Paddle; this application does not create an account or charge you.',
        'Online paid checkout is not active yet. Toss Payments recurring billing supports Korea-issued cards only. Overseas card one-time payment setup is not active.': 'Starter checkout uses Paddle. Toss Payments checkout remains unavailable. Payment methods are shown by Paddle at checkout.',
        'Online paid checkout is not active yet.': NOTICE,
        '온라인 유료 결제는 아직 활성화되지 않았습니다.': 'Starter 월 구독은 Paddle 결제 화면에서 신청할 수 있습니다. 결제 전 최종 금액과 매월 갱신 조건을 확인하세요.',
        'Online payment processing is not active yet. If it is activated, payment method credentials': 'Paddle processes Starter subscription payments. Payment method credentials',
        'Online payment processing is not active yet, so applying for or viewing Starter does not collect payment or activate a paid plan.': 'Paddle checkout is available separately. Applying for or viewing Starter does not collect payment or activate a paid plan.',
        'If online monthly billing is activated, the checkout will disclose': 'Paddle checkout discloses',
        'Online payment processing is not active yet. Viewing the Starter page, applying for Founding Beta, or requesting purchase information does not collect payment or create a paid subscription.': 'Paddle checkout is available for Starter. Viewing the Starter page, applying for Founding Beta, or requesting purchase information does not collect payment or create a paid subscription.',
        'When online payment becomes available, Starter is intended to be offered as a monthly subscription at ₩29,000.': 'Starter is offered through Paddle as a monthly subscription at ₩29,000.',
        'You may request cancellation through the <a href="/contact">Contact page</a>.': 'You can stop renewal from <a href="/subscription/paddle">Manage billing</a> before your next renewal. Cancellation takes effect at the end of the current billing period and does not automatically refund prior payments. You may also request cancellation through the <a href="/contact">Contact page</a>.',
        'Payment integration is not active. Invoices will appear here after a payment provider is connected.': 'No local invoice entries. For Paddle subscription status, open Manage billing. Paddle provides your payment receipt.',
    }
    for old, new in replacements.items():
        source = source.replace(old, new)
    return source
