from .backtest import RunBacktestTool
from .governance import RequestApprovalTool, ValidateResearchTool
from .research import RecordBacktestTool, RegisterResearchTool
from .workspace import ReadWorkspaceFileTool, ReplaceWorkspaceTextTool


def default_tools(workspace):
    return [
        RegisterResearchTool(workspace),
        RecordBacktestTool(workspace),
        RunBacktestTool(workspace),
        ValidateResearchTool(workspace),
        RequestApprovalTool(workspace),
        ReadWorkspaceFileTool(workspace),
        ReplaceWorkspaceTextTool(workspace),
    ]
