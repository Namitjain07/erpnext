from flipkart_integration.flipkart.utils import setup_custom_fields


def after_install():
	setup_custom_fields()


def after_migrate():
	setup_custom_fields()
