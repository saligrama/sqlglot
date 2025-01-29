from __future__ import annotations

from sqlglot import exp, parser
from sqlglot.dialects.dialect import merge_without_target_sql, trim_sql, timestrtotime_sql
from sqlglot.dialects.presto import Presto
from sqlglot.tokens import TokenType
import typing as t


class Trino(Presto):
    SUPPORTS_USER_DEFINED_TYPES = False
    LOG_BASE_FIRST = True

    class Tokenizer(Presto.Tokenizer):
        HEX_STRINGS = [("X'", "'")]

    class Parser(Presto.Parser):
        FUNCTION_PARSERS = {
            **Presto.Parser.FUNCTION_PARSERS,
            "TRIM": lambda self: self._parse_trim(),
            "JSON_QUERY": lambda self: self._parse_json_query(),
            "LISTAGG": lambda self: self._parse_string_agg(),
        }

        JSON_QUERY_OPTIONS: parser.OPTIONS_TYPE = {
            **dict.fromkeys(
                ("WITH", "WITHOUT"),
                (
                    ("WRAPPER"),
                    ("ARRAY", "WRAPPER"),
                    ("CONDITIONAL", "WRAPPER"),
                    ("CONDITIONAL", "ARRAY", "WRAPPED"),
                    ("UNCONDITIONAL", "WRAPPER"),
                    ("UNCONDITIONAL", "ARRAY", "WRAPPER"),
                ),
            ),
        }

        def _parse_json_query_quote(self) -> t.Optional[exp.JSONExtractQuote]:
            if not (
                self._match_text_seq("KEEP", "QUOTES") or self._match_text_seq("OMIT", "QUOTES")
            ):
                return None

            return self.expression(
                exp.JSONExtractQuote,
                option=self._tokens[self._index - 2].text.upper(),
                scalar=self._match_text_seq("ON", "SCALAR", "STRING"),
            )

        def _parse_json_query(self) -> exp.JSONExtract:
            return self.expression(
                exp.JSONExtract,
                this=self._parse_bitwise(),
                expression=self._match(TokenType.COMMA) and self._parse_bitwise(),
                option=self._parse_var_from_options(self.JSON_QUERY_OPTIONS, raise_unmatched=False),
                json_query=True,
                quote=self._parse_json_query_quote(),
            )

    class Generator(Presto.Generator):
        PROPERTIES_LOCATION = {
            **Presto.Generator.PROPERTIES_LOCATION,
            exp.LocationProperty: exp.Properties.Location.POST_WITH,
        }

        TRANSFORMS = {
            **Presto.Generator.TRANSFORMS,
            exp.ArraySum: lambda self,
            e: f"REDUCE({self.sql(e, 'this')}, 0, (acc, x) -> acc + x, acc -> acc)",
            exp.ArrayUniqueAgg: lambda self, e: f"ARRAY_AGG(DISTINCT {self.sql(e, 'this')})",
            exp.LocationProperty: lambda self, e: self.property_sql(e),
            exp.Merge: merge_without_target_sql,
            exp.TimeStrToTime: lambda self, e: timestrtotime_sql(self, e, include_precision=True),
            exp.Trim: trim_sql,
            exp.JSONExtract: lambda self, e: self.jsonextract_sql(e),
        }

        SUPPORTED_JSON_PATH_PARTS = {
            exp.JSONPathKey,
            exp.JSONPathRoot,
            exp.JSONPathSubscript,
        }

        def jsonextract_sql(self, expression: exp.JSONExtract) -> str:
            if not expression.args.get("json_query"):
                return super().jsonextract_sql(expression)

            json_path = self.sql(expression, "expression")
            option = self.sql(expression, "option")
            option = f" {option}" if option else ""

            quote = self.sql(expression, "quote")
            quote = f" {quote}" if quote else ""

            return self.func("JSON_QUERY", expression.this, json_path + option + quote)

        def groupconcat_sql(self, expression: exp.GroupConcat) -> str:
            this = expression.this
            separator = expression.args.get("separator") or exp.Literal.string(",")

            if isinstance(this, exp.Order):
                if this.this:
                    this = this.this.pop()

                on_overflow = self.sql(expression, "on_overflow")
                on_overflow = f" ON OVERFLOW {on_overflow}" if on_overflow else ""
                return f"LISTAGG({self.format_args(this, separator)}{on_overflow}) WITHIN GROUP ({self.sql(expression.this).lstrip()})"

            return super().groupconcat_sql(expression)
