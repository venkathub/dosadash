from decimal import Decimal

from dosadash_api.providers import DemoOtpChannel, MockPaymentProvider
from dosadash_api.providers.otp import mask_phone
from dosadash_shared import OtpChannelType, PaymentStatus


async def test_demo_otp_returns_otp_for_banner():
    result = await DemoOtpChannel().send_otp("+919876543210", "123456")
    assert result.delivered
    assert result.channel == OtpChannelType.DEMO
    assert result.demo_otp == "123456"


def test_mask_phone_redacts():
    assert mask_phone("+919876543210") == "***210"
    assert "+91" not in mask_phone("+919876543210")


async def test_mock_payment_roundtrip():
    provider = MockPaymentProvider()
    order = await provider.create_order(amount=Decimal("249.00"))
    assert order.provider_order_id.startswith("order_mock_")
    assert order.status == PaymentStatus.CREATED

    sig = provider.sign(order_id=order.provider_order_id, payment_id="pay_1")
    assert provider.verify_signature(
        order_id=order.provider_order_id, payment_id="pay_1", signature=sig
    )
    assert not provider.verify_signature(
        order_id=order.provider_order_id, payment_id="pay_1", signature="tampered"
    )

    refund = await provider.refund(payment_id="pay_1", amount=Decimal("249.00"))
    assert refund.status == PaymentStatus.REFUNDED


async def test_mock_signatures_differ_across_secrets():
    a = MockPaymentProvider(secret="a").sign(order_id="o", payment_id="p")
    b = MockPaymentProvider(secret="b").sign(order_id="o", payment_id="p")
    assert a != b
