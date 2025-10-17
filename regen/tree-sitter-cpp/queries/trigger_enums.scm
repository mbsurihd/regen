((comment) @trigger
  .
  (enum_specifier
    name: (type_identifier) @enum.name
    body: (enumerator_list
      (enumerator)) @enumbody ) @enumdef
  (#eq? @trigger "// @regen enum"))
