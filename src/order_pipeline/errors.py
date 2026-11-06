"""可向命令行使用者展示的工程错误。"""


class PipelineError(Exception):
    """数据管道可预期错误。"""


class InputError(PipelineError):
    """输入不存在、无法解码或不符合 CSV 结构。"""


class ConfigurationError(PipelineError):
    """缺少或错误的运行配置。"""


class DatabaseError(PipelineError):
    """连接、事务或 SQL 查询未成功。"""


class OutputError(PipelineError):
    """导出或报告生成未成功。"""
