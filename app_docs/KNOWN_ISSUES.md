# Known Issue Patterns

Use this file as a regression memory list.

## Historical risk patterns

- fixing one gesture mode accidentally changes unrelated gesture behavior
- drawing mode changes break gesture wheel actions
- modal/chooser windows freeze the live camera or hand-driven cursor use
- voice follow-up timing becomes misaligned after a fix
- UI changes sneak in during functional patches
- runtime path/import fixes accidentally alter packaged behavior

## Rule

When a task resembles one of these patterns, explicitly test the matching regression path before finishing.
