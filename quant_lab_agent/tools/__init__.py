from .backtest import RunBacktestTool
from .governance import RequestApprovalTool, ValidateResearchTool
from .research import RecordBacktestTool, RegisterResearchTool
from .workspace import ReadWorkspaceFileTool, ReplaceWorkspaceTextTool
from .market_data import FetchUSMarketDataTool
from .strategy_template import CreateStrategyTemplateTool
from .forecast import ForecastMarketDirectionTool
from .artifacts import ReadArtifactTool


def default_tools(workspace, market_data_api_key: str = "", market_data_provider: str = "alpha_vantage"):
    # Imported lazily to keep the tools package free of a runtime import cycle.
    from ..runtime.artifact_store import ArtifactStore
    artifacts = ArtifactStore(workspace)
    return [
        RegisterResearchTool(workspace),
        RecordBacktestTool(workspace),
        RunBacktestTool(workspace, artifact_store=artifacts),
        ValidateResearchTool(workspace),
        RequestApprovalTool(workspace),
        ReadWorkspaceFileTool(workspace),
        ReplaceWorkspaceTextTool(workspace),
        FetchUSMarketDataTool(workspace, api_key=market_data_api_key, provider=market_data_provider, artifact_store=artifacts),
        CreateStrategyTemplateTool(workspace, artifact_store=artifacts),
        ForecastMarketDirectionTool(workspace, artifact_store=artifacts),
        ReadArtifactTool(artifacts),
    ]
