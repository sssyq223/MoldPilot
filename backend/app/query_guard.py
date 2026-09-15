"""Deliberately narrow SQL grammar. Not enabled as an execution tool before DB scope checks."""
import sqlglot
from sqlglot import exp
from .errors import DomainError


def validate_sql(sql, dataset, columns):
    if len(sql) > 8000: raise DomainError("SQL_REJECTED", "查询超出长度限制")
    try: statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError: raise DomainError("SQL_REJECTED", "SQL语法无效")
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise DomainError("SQL_REJECTED", "只允许单条已实现的只读查询")
    tree = statements[0]
    if tree.args.get("with_") or tree.args.get("into") or tree.args.get("locks") or tree.find(exp.Subquery) or tree.find(exp.Join):
        raise DomainError("SQL_REJECTED", "当前查询子集不开放嵌套、联表或锁定")
    tables = list(tree.find_all(exp.Table))
    if len(tables) != 1 or tables[0].name != dataset or tables[0].db or tables[0].catalog:
        raise DomainError("SQL_REJECTED", "数据集不在当前授权目录中")
    if tree.find(exp.Star): raise DomainError("SQL_REJECTED", "必须显式选择授权字段")
    for col in tree.find_all(exp.Column):
        if col.name not in columns: raise DomainError("SQL_REJECTED", "字段不在授权范围内")
    for fn in tree.find_all(exp.Func):
        if not isinstance(fn, (exp.Count, exp.Sum, exp.Min, exp.Max, exp.Avg)):
            raise DomainError("SQL_REJECTED", "函数尚未开放")
    limit = tree.args.get("limit")
    if limit:
        value = limit.expression
        if not isinstance(value, exp.Literal) or not str(value.this).isdigit() or not 1 <= int(value.this) <= 100:
            raise DomainError("SQL_REJECTED", "查询条数必须在1到100之间")
    else: tree = tree.limit(100)
    return tree
