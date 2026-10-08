# AlgoOS 第一二阶段共同约定

状态：预填草案，供 C0 阅读、A2 检查、S5/A5 共同确认；这里的 JSON 是格式样例，尚非真实运行结果。约定变更由两人同步维护这一份文档。

## 范围与文件位置

单机、C++ 提交、Python 模块、FCFS 起步。一份提交编译一次，按配置顺序运行各测试点。角色与文件目录见 [开工导航](../START_HERE.md)。

- 真实任务的 `source_path` 相对于项目根目录。
- `problem_id` 为 `a_plus_b` 时，读取 `problems/a_plus_b/problem.json`；本期先用这一简单映射。
- 题目配置中的 `input`、`output` 相对于 `problem.json` 所在目录。
- 第一阶段源码为 `examples/a_plus_b.cpp`，测试文件为 `tests/01.in`、`01.out`、`02.in`、`02.out`。第二阶段再增加 03 和题目 JSON。
- 第二阶段每次实际演示使用独立 `run_id`；临时文件在 `work/<run_id>/<task_id>/`，结果在 `artifacts/system/<run_id>/` 或 `artifacts/batch/<run_id>/`。重复运行不能覆盖旧结果，`run_id` 可由运行入口生成，单提交调用由执行模块在内部生成。

## 一份真实任务

```json
{"task_id":"T1","problem_id":"a_plus_b","source_path":"examples/a_plus_b.cpp","enqueue_seq":1}
```

`task_id` 在一批任务中唯一；`enqueue_seq` 是入口按提交先后分配的唯一递增序号。FCFS 只按序号选择，不读取源码或预知运行耗时。第一阶段模拟选择顺序只需要任务编号和序号。

## 两个模块怎样配合

| 接口 | 输入 | 输出 | 约定 |
| --- | --- | --- | --- |
| `pick_next(queued_tasks)` | 待执行任务列表 | 任务编号；空列表返回 `None` | 只选择，不修改列表、不运行程序 |
| `run_task(task)` | 上述真实任务 | 下文的结果字典 | 内部定位题目、编译、运行、比对与汇总 |

批量入口维护待执行列表：选编号 → 查到任务 → 调用执行模块 → 保存结果 → 移除已处理任务。当前一份提交失败时仍继续处理下一份；环境级错误应明确报告原因。两个接口先用普通 Python 函数实现。

## 第二阶段题目配置样例

```json
{
  "problem_id":"a_plus_b",
  "title":"A+B",
  "time_limit_ms":1000,
  "memory_limit_mib":256,
  "tests":[
    {"case_id":"01","input":"tests/01.in","output":"tests/01.out","score":30},
    {"case_id":"02","input":"tests/02.in","output":"tests/02.out","score":30},
    {"case_id":"03","input":"tests/03.in","output":"tests/03.out","score":40}
  ]
}
```

三组输入依次为 `1 2`、`-2 5`、`0 0`，预期输出依次为 `3`、`3`、`0`，均以换行结束。题目总分由测试点分值相加；第一阶段只需前两组文件，不提前创建引用第三组文件的配置。

`time_limit_ms` 表示每个测试点的 CPU 时限，`memory_limit_mib` 单位为 MiB。读到配置不等于已启用限制；第二阶段结果须如实记录启用情况。编译时间、排队时间与单测试点 CPU 时间分别处理，具体时限口径见总方案。

## 正确结果样例

```json
{
  "task_id":"T1","verdict":"AC","score":100,
  "compile_error":"","error_message":"",
  "test_results":[
    {"case_id":"01","verdict":"AC","exit_code":0,"stdout":"3\n"},
    {"case_id":"02","verdict":"AC","exit_code":0,"stdout":"3\n"},
    {"case_id":"03","verdict":"AC","exit_code":0,"stdout":"0\n"}
  ],
  "execution":{"mode":"local_demo","cpu_limit_enforced":false,"memory_limit_enforced":false}
}
```

## 编译失败结果样例

```json
{
  "task_id":"T3","verdict":"CE","score":0,
  "compile_error":"示例：此处保存编译器实际错误输出","error_message":"",
  "test_results":[],
  "execution":{"mode":"local_demo","cpu_limit_enforced":false,"memory_limit_enforced":false}
}
```

编译失败不运行测试点。`compile_error` 保存编译器错误，`error_message` 保存环境或内部执行错误；`stdout` 是程序实际标准输出，`exit_code` 无法取得时为 `null`。`execution` 按实际工具和限制填写，不能直接照抄示例声称完成隔离。

第二阶段先验证 AC、WA、CE、RE。按配置顺序运行全部测试点；全部通过为 AC，否则整份判定采用第一个失败测试点的判定；通过测试点分数单独累加。输出比对只统一换行符，其余文本精确比较。

内部保护超时分别记 `COMPILE_TIMEOUT`、`RUN_TIMEOUT`；配置或工具失败记 `SYSTEM_ERROR` 并填原因，不冒充程序 RE。编译阶段失败时分数为 0、测试点列表为空；无法建立有效评测时分数为 `null`。CPU 时限的 TLE 和内存限制的 MLE 留到下一阶段验证。

## 模拟数据单独约定

模拟器为任务增加 `arrival_ms`、`service_ms`，输出 `start_ms`、`finish_ms`、`wait_ms`、`turnaround_ms`，统一使用毫秒。入队序号按到达顺序分配，同刻到达按固定顺序。

模拟时钟只向选择器提供已到达任务；服务时长供模拟器推进时钟，FCFS 不用它排序。CSV 增加 `data_source=simulation`，写到 `artifacts/simulation/`。真实四任务演示输出真实判定，不能混入模拟值。
