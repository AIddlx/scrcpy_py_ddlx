# 变更日志目录

本目录存放所有重大变更的详细日志，确保变更可追溯、可回滚。

---

## 命名规范

```
REFACTOR_YYYYMMDD_简述.md    # 重构类变更
FIX_YYYYMMDD_简述.md         # 修复类变更
FEATURE_YYYYMMDD_简述.md     # 新功能类变更
```

## 使用方法

### 1. 创建变更日志

```bash
# 复制模板
cp docs/development/changelog/TEMPLATE.md docs/development/changelog/REFACTOR_20260221_你的变更.md

# 编辑填写
```

### 2. 重构前

1. 运行接口契约测试确认基线：
   ```bash
   python tests_gui/test_interface_contracts.py
   ```

2. 创建备份 tag：
   ```bash
   python scripts/local_backup.py before_xxx_refactor
   ```

3. 创建变更日志文件

### 3. 重构后

1. 再次运行接口契约测试：
   ```bash
   python tests_gui/test_interface_contracts.py
   ```

2. 更新变更日志，记录测试结果

3. 如果测试通过，提交变更

---

## 当前变更日志

| 文件 | 日期 | 状态 | 说明 |
|------|------|------|------|
| [REFACTOR_20260221_QOPENGLWINDOW.md](REFACTOR_20260221_QOPENGLWINDOW.md) | 2026-02-21 | 规划中 | QOpenGLWindow 重构计划 |

---

## 相关文档

- [REFACTOR_SAFETY_GUIDE.md](../REFACTOR_SAFETY_GUIDE.md) - 重构安全保障指南
- [INTERFACE_CONTRACTS.md](../INTERFACE_CONTRACTS.md) - 接口契约定义
