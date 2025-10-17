from dataclasses import dataclass
import argparse as AP
from importlib.resources import files

import tree_sitter as TS
import tree_sitter_cpp as TSCPP

CPP_LANG = TS.Language(TSCPP.language())


PKG_ROOT = files("regen")
TEMPLATES_DIR = PKG_ROOT / "templates"
QUERIES_DIR = PKG_ROOT / "tree-sitter-cpp/queries"


@dataclass
class CppEnum:
  name: TS.Node
  namespaces: list[TS.Node]
  value_names: list[TS.Node]


# Parse and Extract
def get_outer_ns_nodes(def_node: TS.Node):
  parent = def_node.parent
  ns_nodes: list[TS.Node] = []
  while parent is not None:
    if parent.type == "namespace_definition":
      ns_name = parent.child_by_field_name("name")
      assert ns_name
      ns_nodes.append(ns_name)
    parent = parent.parent
  return ns_nodes


def get_enum_value_name_nodes(body_node: TS.Node):
  name_nodes: list[TS.Node] = []
  for inner in body_node.children:
    if inner.type == "enumerator":
      name_nodes.append(inner.children[0])
  return name_nodes


def extract_enums(root: TS.Node):
  query_str: str = ""
  with (QUERIES_DIR / "trigger_enums.scm").open("r") as file:
    query_str = file.read()

  query = TS.Query(CPP_LANG, query_str)
  cursor = TS.QueryCursor(query)
  enums: list[CppEnum] = []
  for match in cursor.matches(root):
    capture = match[1]
    name_node = capture["enum.name"][0]
    body_node = capture["enumbody"][0]
    def_node = capture["enumdef"][0]

    enums.append(
      CppEnum(
        name_node,
        get_outer_ns_nodes(def_node),
        get_enum_value_name_nodes(body_node),
      )
    )
  return enums


# Build Strings
def build_q_type(enum: CppEnum):
  q_type: str = ""
  for ns in reversed(enum.namespaces):
    assert ns.text
    q_type = q_type + f"::{ns.text.decode()}"
  assert enum.name.text
  q_type += f"::{enum.name.text.decode()}"
  return q_type


# Templates
STROF_OUTER = """\
constexpr std::string_view stringof(const {q_type}& v) noexcept {{
  switch(v) {{
{case_l}
  }};
}}
"""
STROF_INNER = '    case {q_type}::{v_name}: return "{uq_type}::{v_name}";'
WSTROF_OUTER = """\
constexpr std::wstring_view wstringof(const {q_type}& v) noexcept {{
  switch(v) {{
{case_l}
  }};
}}
"""
WSTROF_INNER = '    case {q_type}::{v_name}: return L"{uq_type}::{v_name}";'
FMT_REQUIREMENT = "OneOf<T, {q_type_l}>"


def build_strof(enum: CppEnum):
  q_type = build_q_type(enum)
  assert enum.name.text
  uq_type = enum.name.text.decode()

  def build_case(v: TS.Node):
    assert v.text
    v_name = v.text.decode()
    return STROF_INNER.format(q_type=q_type, v_name=v_name, uq_type=uq_type)

  cases = map(build_case, enum.value_names)
  case_l = "\n".join(cases)
  return STROF_OUTER.format(q_type=q_type, case_l=case_l)


def build_wstrof(enum: CppEnum):
  q_type = build_q_type(enum)
  assert enum.name.text
  uq_type = enum.name.text.decode()

  def build_case(v: TS.Node):
    assert v.text
    v_name = v.text.decode()
    return WSTROF_INNER.format(q_type=q_type, v_name=v_name, uq_type=uq_type)

  cases = map(build_case, enum.value_names)
  case_l = "\n".join(cases)
  return WSTROF_OUTER.format(q_type=q_type, case_l=case_l)


def build_fmt_requirement(enums: list[CppEnum]):
  q_types = map(build_q_type, enums)
  q_type_l = ", ".join(q_types)
  return FMT_REQUIREMENT.format(q_type_l=q_type_l)


def read_output_file_name(input_file: str):
  top_line: str = ""
  with open(input_file, "r") as file:
    top_line = file.readline()
  assert top_line != ""
  if top_line.startswith("// @regen output ") and len(top_line) > len(
    "// @regen output "
  ):
    sep_last = top_line.rfind(" ")
    if sep_last != -1:
      return top_line[sep_last:].strip()
  return ""


def init_cmd(input_file: str):
  output_file = read_output_file_name(input_file)
  if output_file == "":
    print("<input_file> must have at the top `// @regen output <output_file>`")
    return -1
  init_templ: str = ""
  with (TEMPLATES_DIR / "init.hpp.templ").open("r") as file:
    init_templ = file.read()
  with open(output_file, "w") as file:
    _ = file.write(init_templ.format(input_file=f'"{input_file}"'))
  return 0


def gen_cmd(input_file: str):
  output_file = read_output_file_name(input_file)
  if output_file == "":
    print("<input_file> must have at the top `// @regen output <output_file>`")
    return -1

  input_code: str = ""
  with open(input_file, "r") as file:
    input_code = file.read()

  parser = TS.Parser(CPP_LANG)
  parse_tree = parser.parse(input_code.encode())
  enums = extract_enums(parse_tree.root_node)
  if len(enums) < 1:
    return 0
  strofs = map(build_strof, enums)
  wstrofs = map(build_wstrof, enums)
  fmt_requirement = build_fmt_requirement(enums)
  strof_l = "\n\n".join(strofs)
  wstrof_l = "\n\n".join(wstrofs)

  gen_templ: str = ""
  with (TEMPLATES_DIR / "gen.hpp.templ").open("r") as file:
    gen_templ = file.read()

  with open(output_file, "w") as file:
    _ = file.write(
      gen_templ.format(
        input_file=f'"{input_file}"',
        strof_l=strof_l,
        wstrof_l=wstrof_l,
        fmt_requirement=fmt_requirement,
      )
    )
  return 0


def main() -> int:
  arg_parser = AP.ArgumentParser(
    description="Generate extra code for C++ enum classes.",
    usage="regen.py <command> <input_file>",
    epilog="""Create a header file where you intend to add enum class definitions and add at the top (before pragma once and ifndef) `// @regen output <output_file>`. Then run `regen init <input_file>` to generate a stub header file. include and use this header file to avoid lsp errors. Run `regen gen <input_file>` before you compile to actually generate the code.""",
  )

  sub_parsers = arg_parser.add_subparsers(
    title="Available Commands", required=True
  )
  init_parser = sub_parsers.add_parser(
    "init", help="Generate a stub file to avoid lsp errors"
  )
  _ = init_parser.add_argument("input_file")
  _ = init_parser.set_defaults(func=init_cmd)

  gen_parser = sub_parsers.add_parser(
    "gen", help="Generated code and write to {output}"
  )
  _ = gen_parser.add_argument("input_file")
  _ = gen_parser.set_defaults(func=gen_cmd)

  args = arg_parser.parse_args()
  args.func(args.input_file)
  return 0


if __name__ == "__main__":
  exit(main())
