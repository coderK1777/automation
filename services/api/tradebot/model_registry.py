"""Import every persistent model for migrations and isolated test databases."""
from tradebot import (  # noqa: F401
    models, data_models, demo_models, expansion_models, news_models,
    notifications_models, reporting_models, research_models, security_admin_models, strategy_risk_models,
)
