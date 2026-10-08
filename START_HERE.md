# AlgoOS 开工导航

先用约 10 分钟看懂这页，再打开自己的 Todo。总方案用于查背景，日常按“当前任务 → 对应文件 → 运行检查”推进。

## C0 两人一起认识项目 30—45 分钟

- [ ] 确认各自在 Linux 中使用的项目根目录，以及如何交换同一版本的文件；下文所有代码路径都相对于这个根目录。
- [ ] 对着下面的目录图，指出自己的代码、队友的代码和输入数据分别放在哪里。
- [ ] 一起读“运行流程”，口头走通一次任务从提交到结果的过程。
- [ ] 打开 [共同约定](docs/stage1_agreement.md)，看懂一份任务和两种结果的样例；细节由第一阶段 A2 检查、S5/A5 共同确认。

C0 是同一次共同任务，已计入两份第一阶段 Todo 的工时。完成后，你做 S1、S2，队友做 A1、A2。项目目录、包标记和文档入口已建立；业务代码与运行结果仍按 Todo 逐步完成。

## 文件放在哪里

```text
项目根目录/
├── README.md             GitHub 首页与资料索引
├── START_HERE.md         开工入口
├── progress.md           两人共同维护的开发过程记录
├── plan_revised.md       背景、范围和十周安排
├── system_list/          系统负责人的 Todo
├── algorithm_list/       算法负责人的 Todo
├── examples/             被评测的 C++ 程序
├── problems/a_plus_b/     题目配置待填写，tests/ 已建立
├── judge/                编译、运行、比对、汇总
├── scheduler/            FCFS、模拟时钟、指标计算
├── scripts/              命令行与演示入口
├── docs/                 共同约定、环境与阶段记录
│   └── templates/        课程报告 Word 模板
├── artifacts/            system/、simulation/、batch/ 运行结果
├── work/                 临时编译与运行文件
└── old/                  原始方案
```

`examples` 是交给评测机的程序；`judge` 是评测机自己的代码；`scripts` 是把各模块接起来的入口。模拟数据放到 `artifacts/simulation/`，真实评测结果放到另外两个子目录，分别保留来源。

目录与 `judge/`、`scheduler/`、`scripts/` 中的空 `__init__.py` 已建立；具体业务文件按 Todo 创建。空目录中的 `.gitkeep` 用于让 Git 保存目录。统一从项目根目录运行 `python3 -m scripts.入口名`；下列命令在对应 Todo 实现完成后才可用。

## 程序怎样运行

第一阶段你运行 `python3 -m scripts.run_demo`：Python 调用编译器 → 给 A+B 程序传入两组输入 → 打印输出。队友独立运行 `python3 -m scripts.demo_fcfs`：模拟任务列表 → FCFS → 打印选择顺序。

第二阶段共同运行 `python3 -m scripts.demo_batch`：准备四份任务 → `pick_next` 选编号 → 找到对应任务 → `run_task` 编译与评测 → 保存结果 → 移除已处理任务 → 继续下一份。

| 核心文件 | 职责 | 负责人 |
| --- | --- | --- |
| `scheduler/fcfs.py` | `pick_next(queued_tasks)` 选择下一任务编号 | 队友 |
| `judge/service.py` | `run_task(task)` 汇总编译与各测试点判定 | 你 |
| `scripts/demo_batch.py` | 调用上述函数，组织四任务演示 | 队友主写，共同联调 |
| `docs/stage1_agreement.md` | 字段、样例和路径规则的统一依据 | 共同确认，修改时相互同步 |

初期两个模块在同一个 Python 项目中直接调用。第一阶段允许把执行流程写在 `run_demo.py` 中，第二阶段再逐步拆入 `judge/`。任务是一份提交；测试点是该提交要运行的一对输入和预期输出；评测结果记录这份提交的判定。

## 现在打开哪份清单

| 时间 | 系统负责人 | 算法负责人 |
| --- | --- | --- |
| 10 月 7—9 日 | [第一阶段系统 Todo](system_list/todo_stage1_system.md) | [第一阶段算法 Todo](algorithm_list/todo_stage1_algorithm.md) |
| 10 月 10—25 日 | [第二阶段系统 Todo](system_list/todo_stage2_system.md) | [第二阶段算法 Todo](algorithm_list/todo_stage2_algorithm.md) |

每次只做当前编号的一项，运行检查通过后再勾选。完成任务、遇到卡点或作出共同决定时，在根目录 [开发过程记录](progress.md) 追加一条，写清任务编号、实际验证结果和下一步；未通过检查的任务如实标记为进行中或受阻。卡住时保留任务编号、相关文件、运行命令、预期结果和实际错误，围绕这个小问题求助；字段有变化先改共同约定，再同步调用方。

课程报告模板已整理到 [docs/templates/](docs/templates/创新创业实训课程报告模板.docx)。Git 提交范围与忽略规则见 [README](README.md)。
