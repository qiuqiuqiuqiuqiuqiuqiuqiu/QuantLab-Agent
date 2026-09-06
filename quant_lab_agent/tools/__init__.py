from .backtest import RunBacktestTool
from .governance import RequestApprovalTool, ValidateResearchTool
from .research import RecordBacktestTool, RegisterResearchTool
from .workspace import ReadWorkspaceFileTool, ReplaceWorkspaceTextTool
from .market_data import FetchUSMarketDataTool
from .strategy_template import CreateStrategyTemplateTool
from .forecast import ForecastMarketDirectionTool


def default_tools(workspace, market_data_api_key: str = "", market_data_provider: str = "alpha_vantage"):
    return [
        RegisterResearchTool(workspace),
        RecordBacktestTool(workspace),
        RunBacktestTool(workspace),
        ValidateResearchTool(workspace),
        RequestApprovalTool(workspace),
        ReadWorkspaceFileTool(workspace),
        ReplaceWorkspaceTextTool(workspace),
        FetchUSMarketDataTool(workspace, api_key=market_data_api_key, provider=market_data_provider),
        CreateStrategyTemplateTool(workspace),
        ForecastMarketDirectionTool(workspace),
    ]
