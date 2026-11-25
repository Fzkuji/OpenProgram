"""The plain-code rules of method_lint: methodology errors read from the experiment
code's syntax tree, as scicode-lint and LeakageDetector find them. No model calls.

Each rule has an id, a severity, an explanation and a fix. A finding is
{file, line, rule, severity, finding, fix}; the finding names the code it saw and
carries no line number, so a rerun matches it to the row the author already marked.
"""

import ast
import re

RULES = {
    "preprocess-before-split": {
        "severity": "high",
        "explanation": "标准化、编码、向量化、PCA、缺失值填补在切分训练/测试之前就在全部数据上拟合，测试集的统计量漏进了训练。",
        "fix": "先切分，只在训练集上 fit，再对验证/测试集 transform；或把预处理放进 Pipeline 交给交叉验证。"},
    "selection-before-split": {
        "severity": "high",
        "explanation": "特征选择在切分之前用了全部数据（含测试集的标签），选出的特征偏向测试集。",
        "fix": "把特征选择放到切分之后只在训练集上做，或放进 Pipeline 里随交叉验证每折重做。"},
    "test-in-tuning": {
        "severity": "high",
        "explanation": "超参数搜索或早停的循环里读了测试集，测试集参与了选择，报告的测试分数偏乐观。",
        "fix": "从训练集再分出验证集给搜索和早停用；测试集只在最后评估一次。"},
    "target-leakage": {
        "severity": "high",
        "explanation": "标签列（或名字明显是标签/未来信息的列）进了特征，模型直接看到了答案。",
        "fix": "构造特征时去掉标签列和由标签派生的列，例如 df.drop(columns=[标签列])。"},
    "no-seed": {
        "severity": "medium",
        "explanation": "代码用了随机性（打乱、随机初始化、随机切分）却没有设随机种子，结果无法复现。",
        "fix": "在入口处设种子：random.seed、np.random.seed、torch.manual_seed，sklearn 传 random_state。"},
    "shuffled-cv-time-series": {
        "severity": "medium",
        "explanation": "数据有日期/时间列，却用打乱的切分或交叉验证，未来的数据进了训练集。",
        "fix": "按时间切分：TimeSeriesSplit，或 train_test_split(..., shuffle=False) 按时间排好序再切。"},
    "evaluate-on-train": {
        "severity": "high",
        "explanation": "评估指标只在训练数据上算，没有在验证/测试集上算，报告的是拟合程度而不是泛化。",
        "fix": "在留出的验证/测试集上计算指标并报告。"},
    "duplicates-unchecked": {
        "severity": "low",
        "explanation": "切分前没有检查重复样本，同一条数据可能同时出现在训练集和测试集。",
        "fix": "切分前 df.drop_duplicates()，或切分后检查训练集与测试集的交集为空。"},
    "metric-same-split": {
        "severity": "high",
        "explanation": "指标所用的预测来自在同一份数据上训练的模型：模型在哪份数据上 fit，就在哪份数据上评估。",
        "fix": "在训练时没见过的数据上预测再算指标，或用交叉验证。"},
}

PREPROCESSORS = {"StandardScaler", "MinMaxScaler", "RobustScaler", "MaxAbsScaler", "Normalizer", "OneHotEncoder",
                 "OrdinalEncoder", "TargetEncoder", "CountVectorizer", "TfidfVectorizer", "TfidfTransformer", "PCA",
                 "TruncatedSVD", "KernelPCA", "IncrementalPCA", "SimpleImputer", "KNNImputer", "IterativeImputer",
                 "QuantileTransformer", "PowerTransformer", "KBinsDiscretizer", "FactorAnalysis", "FastICA", "NMF",
                 "LabelBinarizer", "MultiLabelBinarizer"}
PREPROCESSOR_NAME = re.compile(r"scaler|encoder|vectori[sz]er|pca|imputer|normali[sz]er|tfidf|svd", re.I)
SELECTORS = {"SelectKBest", "SelectPercentile", "SelectFpr", "SelectFdr", "SelectFwe", "GenericUnivariateSelect",
             "RFE", "RFECV", "SelectFromModel", "SequentialFeatureSelector", "Boruta", "BorutaPy"}
SELECTOR_FUNCS = {"mutual_info_classif", "mutual_info_regression", "f_classif", "f_regression", "chi2",
                  "r_regression"}
SPLITTERS = {"KFold", "StratifiedKFold", "GroupKFold", "StratifiedGroupKFold", "RepeatedKFold",
             "RepeatedStratifiedKFold", "ShuffleSplit", "StratifiedShuffleSplit", "GroupShuffleSplit", "LeaveOneOut",
             "LeavePOut", "TimeSeriesSplit", "PredefinedSplit"}
SPLIT_FUNCS = {"train_test_split", "cross_val_score", "cross_validate", "cross_val_predict", "random_split"}
SEARCHERS = {"GridSearchCV", "RandomizedSearchCV", "HalvingGridSearchCV", "HalvingRandomSearchCV", "BayesSearchCV"}
METRICS = {"accuracy_score", "balanced_accuracy_score", "f1_score", "precision_score", "recall_score",
           "roc_auc_score", "average_precision_score", "log_loss", "mean_squared_error", "mean_absolute_error",
           "root_mean_squared_error", "r2_score", "matthews_corrcoef", "classification_report", "top_k_accuracy_score",
           "mean_absolute_percentage_error", "explained_variance_score", "cohen_kappa_score", "jaccard_score"}
EVALUATE = {"score", "evaluate"}
PREDICT = {"predict", "predict_proba", "decision_function", "predict_log_proba"}
SKLEARN_RANDOM = re.compile(r"^(RandomForest|ExtraTrees|GradientBoosting|HistGradientBoosting|MLP|SGD|KMeans|"
                            r"MiniBatchKMeans|Bagging|AdaBoost|DecisionTree|TSNE|RandomizedSearchCV|ShuffleSplit|"
                            r"StratifiedShuffleSplit|GroupShuffleSplit|train_test_split)")
SEED_HELPER = re.compile(r"^((set|fix|init)_?(random_?|global_?)?seeds?|seed_?(everything|all|torch|worker))$", re.I)
TORCH_RANDOM = {"rand", "randn", "randint", "randperm", "rand_like", "randn_like", "bernoulli", "multinomial",
                "normal"}
PY_RANDOM = {"random", "randint", "randrange", "choice", "choices", "shuffle", "sample", "uniform", "gauss"}
TIME_WORDS = {"date", "time", "timestamp", "datetime", "year", "month", "day", "week", "hour", "dt"}
LEAKY = re.compile(r"^(target|targets|label|labels|y|outcome|ground_?truth|gt|answer|class|is_fraud|churn)$|"
                   r"_(target|label)$|^(future|next)_|leak", re.I)
TRAIN_WORDS = {"train", "training", "tr", "trn", "xtr", "ytr", "xtrain", "ytrain"}
TEST_WORDS = {"test", "tst", "te", "testing", "holdout", "xtest", "ytest", "xte", "yte"}
HELDOUT_WORDS = TEST_WORDS | {"val", "valid", "validation", "dev", "eval", "xval", "yval", "heldout", "oof"}
TARGET_WORDS = {"y", "label", "labels", "target", "targets"}
FEATURE_WORDS = {"x", "features", "feature", "inputs", "predictors", "cols", "columns", "feature_cols"}


def words(name: str) -> set[str]:
    """The lower-case words of an identifier: X_train -> {x, train}, testLoader -> {test, loader}."""
    parts = re.split(r"[_\W\d]+|(?<=[a-z])(?=[A-Z])", name)
    return {p.lower() for p in parts if p} | {name.lower()}


def names(node) -> list[str]:
    """Identifiers read in node: variable names and attribute names."""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.append(n.id)
        elif isinstance(n, ast.Attribute):
            out.append(n.attr)
    return out


def tagged(node, vocabulary: set[str]) -> bool:
    return any(words(n) & vocabulary for n in names(node))


def func_name(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def dotted(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001  an odd node is simply not named
        return ""


def keyword(call: ast.Call, name: str):
    return next((k.value for k in call.keywords if k.arg == name), None)


def constant(node, value) -> bool:
    return isinstance(node, ast.Constant) and node.value == value


def short(node, limit: int = 70) -> str:
    text = " ".join(dotted(node).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def scopes(tree: ast.Module) -> list:
    return [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]


def in_scope(scope) -> list:
    """The nodes of a scope in source order, not entering nested functions or classes."""
    out, stack = [], list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        out.append(node)
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(node))
    return sorted((n for n in out if hasattr(n, "lineno")), key=lambda n: (n.lineno, n.col_offset))


def classes(tree: ast.Module) -> dict[str, str]:
    """What each assigned name was built from: scaler = StandardScaler() -> {"scaler": "StandardScaler"};
    a Pipeline counts as its first preprocessor or selector."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            built = func_name(node.value)
            if built in ("Pipeline", "make_pipeline", "ColumnTransformer", "make_column_transformer"):
                inner = [func_name(c) for c in ast.walk(node.value) if isinstance(c, ast.Call)]
                built = next((c for c in inner if c in PREPROCESSORS | SELECTORS), built)
            for target in node.targets:
                out[dotted(target)] = built
    return out


class File:
    """One parsed file and the findings of every rule on it."""

    def __init__(self, rel: str, text: str):
        self.rel, self.text = rel, text
        self.tree = ast.parse(text)
        self.built = classes(self.tree)
        self.calls = sorted((n for n in ast.walk(self.tree) if isinstance(n, ast.Call)),
                            key=lambda n: (n.lineno, n.col_offset))
        self.found: list[dict] = []

    def add(self, line: int, rule: str, finding: str) -> None:
        self.found.append({"file": self.rel, "line": line, "rule": rule, "severity": RULES[rule]["severity"],
                           "finding": finding, "fix": RULES[rule]["fix"]})

    def kind(self, call: ast.Call) -> str:
        """The class a method call's receiver was built from ("" when unknown)."""
        if not isinstance(call.func, ast.Attribute):
            return ""
        receiver = call.func.value
        if isinstance(receiver, ast.Call):
            return func_name(receiver)
        built = self.built.get(dotted(receiver), "")
        if built:
            return built
        last = dotted(receiver).split(".")[-1]
        return "preprocessor" if PREPROCESSOR_NAME.search(last) else ""

    def is_split(self, call: ast.Call) -> bool:
        name = func_name(call)
        if name in SPLIT_FUNCS:
            return True
        if name == "split" and self.kind(call) in SPLITTERS:
            return True
        return name == "fit" and self.kind(call) in SEARCHERS

    def run(self) -> list[dict]:
        for rule in (self.before_split, self.test_in_tuning, self.target_leakage, self.evaluate_on_train,
                     self.same_split):
            rule()
        return self.found

    # preprocess-before-split, selection-before-split
    def before_split(self) -> None:
        for scope in scopes(self.tree):
            nodes = [n for n in in_scope(scope) if isinstance(n, ast.Call)]
            splits = [n.lineno for n in nodes if self.is_split(n)]
            for call in nodes:
                name = func_name(call)
                later = [s for s in splits if s > call.lineno]
                if not later:
                    continue
                if name in SELECTOR_FUNCS:
                    self.add(call.lineno, "selection-before-split",
                             f"`{short(call)}` 在全部数据上算特征得分，之后才切分训练/测试")
                    continue
                if name not in ("fit", "fit_transform") or not call.args or tagged(call.args[0], TRAIN_WORDS):
                    continue
                kind = self.kind(call)
                if kind in SELECTORS:
                    self.add(call.lineno, "selection-before-split",
                             f"`{short(call)}`（{kind}）在全部数据上做特征选择，之后才切分训练/测试")
                elif kind in PREPROCESSORS or kind == "preprocessor":
                    what = kind if kind != "preprocessor" else "预处理器"
                    self.add(call.lineno, "preprocess-before-split",
                             f"`{short(call)}`（{what}）在全部数据上拟合，之后才切分训练/测试")

    # test-in-tuning
    def test_in_tuning(self) -> None:
        seen = set()
        for loop in ast.walk(self.tree):
            if not isinstance(loop, (ast.For, ast.While)):
                continue
            body = [n for stmt in loop.body for n in ast.walk(stmt)]
            if not (self.searches(loop) or self.stops_early(body)):
                continue
            for call in (n for n in body if isinstance(n, ast.Call)):
                used = [a for a in [*call.args, *(k.value for k in call.keywords)] if tagged(a, TEST_WORDS)]
                if used and call.lineno not in seen:
                    seen.add(call.lineno)
                    what = "超参数搜索" if self.searches(loop) else "早停"
                    self.add(call.lineno, "test-in-tuning",
                             f"{what}循环里 `{short(call)}` 用了测试集 `{short(used[0], 40)}`")
                    break

    @staticmethod
    def searches(loop) -> bool:
        if not isinstance(loop, ast.For):
            return False
        it = loop.iter
        if isinstance(it, (ast.List, ast.Tuple)) and len(it.elts) > 1:
            return True
        if isinstance(it, ast.Call) and func_name(it) in ("ParameterGrid", "ParameterSampler", "product"):
            return True
        target = names(loop.target)
        return any(words(n) & {"lr", "alpha", "params", "param", "c", "depth", "hp", "config", "trial"} for n in target)

    @staticmethod
    def stops_early(body) -> bool:
        for n in body:
            if isinstance(n, (ast.Compare, ast.AugAssign, ast.Assign)) and any(
                    words(x) & {"best", "patience", "early", "improve", "stop"} for x in names(n)):
                return True
        return False

    # target-leakage
    def target_leakage(self) -> None:
        labels = {}            # frame -> label column
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = dotted(node.targets[0])
            value = node.value
            if words(target) & TARGET_WORDS:
                frame, column = self.column(value)
                if frame and column and not (isinstance(value, ast.Call) and func_name(value) == "pop"):
                    labels[frame] = column
            if words(target) & FEATURE_WORDS and isinstance(value, (ast.List, ast.Tuple)):
                for elt in value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str) and LEAKY.search(elt.value):
                        self.add(node.lineno, "target-leakage",
                                 f"特征列表 `{target}` 里有列 \"{elt.value}\"，名字像标签或未来信息")
        if not labels:
            return
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = dotted(node.targets[0])
            if not (words(target) & FEATURE_WORDS) or words(target) & TARGET_WORDS:
                continue
            leak = self.keeps_label(node.value, labels)
            if leak:
                self.add(node.lineno, "target-leakage", f"`{target} = {short(node.value, 50)}` 保留了标签列 \"{leak}\"")

    @staticmethod
    def column(value) -> tuple[str, str]:
        """(frame, column) for df["col"], df.col, df[["col"]], df.pop("col")."""
        if isinstance(value, ast.Subscript):
            key = value.slice
            if isinstance(key, ast.List) and len(key.elts) == 1:
                key = key.elts[0]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                return dotted(value.value), key.value
        if isinstance(value, ast.Call) and func_name(value) == "pop" and isinstance(value.func, ast.Attribute):
            if value.args and isinstance(value.args[0], ast.Constant):
                return dotted(value.func.value), str(value.args[0].value)
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.attr not in (
                "values", "shape", "columns", "index", "iloc", "loc"):
            return value.value.id, value.attr
        return "", ""

    @staticmethod
    def keeps_label(value, labels: dict[str, str]) -> str:
        base = value
        if isinstance(base, ast.Call) and func_name(base) == "to_numpy" and isinstance(base.func, ast.Attribute):
            base = base.func.value
        if isinstance(base, ast.Attribute) and base.attr == "values":
            base = base.value
        frame = dotted(base)
        if frame in labels:
            return labels[frame]
        if isinstance(base, ast.Call) and func_name(base) == "drop" and isinstance(base.func, ast.Attribute):
            frame = dotted(base.func.value)
            dropped = {c.value for c in ast.walk(base) if isinstance(c, ast.Constant)}
            if frame in labels and labels[frame] not in dropped:
                return labels[frame]
        if isinstance(base, ast.Subscript) and isinstance(base.slice, ast.List):
            frame = dotted(base.value)
            chosen = {c.value for c in base.slice.elts if isinstance(c, ast.Constant)}
            if frame in labels and labels[frame] in chosen:
                return labels[frame]
        return ""

    # shuffled-cv-time-series: the signal here, the rule over all files
    def timed(self) -> bool:
        """Whether the data has date/time columns: a column name like "date" or
        "order_time", pd.to_datetime or parse_dates."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 40 \
                    and re.fullmatch(r"[A-Za-z_]+", node.value) and words(node.value) & TIME_WORDS:
                return True
            if isinstance(node, ast.Call) and (func_name(node) == "to_datetime" or keyword(node, "parse_dates")):
                return True
        return False

    # evaluate-on-train
    def evaluations(self) -> list[ast.Call]:
        return [c for c in self.calls if func_name(c) in METRICS or func_name(c) in EVALUATE]

    def evaluate_on_train(self) -> None:
        evals = self.evaluations()
        held = [c for c in evals if any(tagged(a, HELDOUT_WORDS) for a in c.args)]
        train = [c for c in evals if any(tagged(a, TRAIN_WORDS) for a in c.args)]
        if train and not held:
            self.add(train[0].lineno, "evaluate-on-train", f"`{short(train[0])}` 在训练数据上算指标，没有留出集上的指标")

    # metric-same-split
    def same_split(self) -> None:
        metric_args = [a for c in self.evaluations() if func_name(c) in METRICS for a in c.args]
        used = {n for a in metric_args for n in names(a)}
        for scope in scopes(self.tree):
            nodes = in_scope(scope)
            fitted = {}
            for node in nodes:
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or not node.args:
                    continue
                name, receiver = func_name(node), dotted(node.func.value)
                if name == "fit" and self.kind(node) not in PREPROCESSORS | SELECTORS | SEARCHERS:
                    fitted[receiver] = dotted(node.args[0])
                elif name in PREDICT | EVALUATE and fitted.get(receiver) == dotted(node.args[0]):
                    data = node.args[0]
                    if tagged(data, TRAIN_WORDS) or not self.scored(node, metric_args, used, nodes):
                        continue
                    self.add(node.lineno, "metric-same-split",
                             f"`{receiver}` 在 `{short(data, 30)}` 上 fit，又在同一份 `{short(data, 30)}` 上 "
                             f"`{name}` 算指标")

    @staticmethod
    def scored(call, metric_args, used, nodes) -> bool:
        if func_name(call) in EVALUATE:
            return True
        if any(call in list(ast.walk(a)) for a in metric_args):
            return True
        for n in nodes:
            if isinstance(n, ast.Assign) and n.value is call:
                return any(dotted(t) in used for t in n.targets)
        return False


# Rules over all the files together ------------------------------------------------------

def randomness(files: list[File]) -> list[dict]:
    """no-seed: per library, the first use of randomness when no file sets its seed."""
    seeded, first = set(), {}
    for f in files:
        aliases = {a.asname or a.name: a.name for n in ast.walk(f.tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                   for a in n.names}
        for call in f.calls:
            path, name = dotted(call.func), func_name(call)
            head = path.split(".")[0]
            module = aliases.get(head, head)
            if SEED_HELPER.match(name):
                seeded |= {"random", "numpy", "torch", "sklearn"}
            elif path in ("random.seed",) or module == "random" and name == "seed":
                seeded.add("random")
            elif name == "seed" and ".random" in path or name in ("default_rng", "RandomState") and call.args:
                seeded.add("numpy")
            elif name in ("manual_seed", "manual_seed_all"):
                seeded.add("torch")
            use = ""
            if module == "random" and name in PY_RANDOM and path.count(".") == 1:
                use = "random"
            elif ".random." in f".{path}" and module in ("numpy", "np") and name not in ("seed", "default_rng",
                                                                                          "RandomState"):
                use = "numpy"
            elif name == "default_rng" and not call.args:
                use = "numpy"
            elif module == "torch" and name in TORCH_RANDOM or (name == "DataLoader" and constant(keyword(call, "shuffle"), True)) \
                    or "nn" in path.split(".")[:-1] and name[:1].isupper():
                use = "torch"
            elif SKLEARN_RANDOM.match(name) or name in ("KFold", "StratifiedKFold") and constant(keyword(call, "shuffle"), True):
                state = keyword(call, "random_state")
                if state is None or constant(state, None):
                    use = "sklearn"
            if use and use not in first:
                first[use] = (f, call)
    if "numpy" in seeded:
        seeded.add("sklearn")       # sklearn falls back on numpy's global state
    out = []
    labels = {"random": "Python random", "numpy": "NumPy", "torch": "PyTorch", "sklearn": "scikit-learn"}
    for lib, (f, call) in first.items():
        if lib not in seeded:
            f.add(call.lineno, "no-seed", f"用了 {labels[lib]} 的随机性 `{short(call)}`，但没有设种子")
            out.append(f.found[-1])
    return out


def time_series(files: list[File]) -> list[dict]:
    """shuffled-cv-time-series: some file reads date/time columns, none uses
    TimeSeriesSplit, and a split shuffles."""
    if not any(f.timed() for f in files) or any(func_name(c) == "TimeSeriesSplit" for f in files for c in f.calls):
        return []
    for f in files:
        for call in f.calls:
            name = func_name(call)
            if name in ("KFold", "StratifiedKFold", "RepeatedKFold") and constant(keyword(call, "shuffle"), True) \
                    or name in ("ShuffleSplit", "StratifiedShuffleSplit") \
                    or name == "train_test_split" and not constant(keyword(call, "shuffle"), False):
                f.add(call.lineno, "shuffled-cv-time-series", f"数据有日期/时间列，`{short(call)}` 打乱后切分")
                return [f.found[-1]]
    return []


def duplicates(files: list[File]) -> list[dict]:
    """duplicates-unchecked: a split is made and no file checks for duplicate rows."""
    checks = ("drop_duplicates", "duplicated", "unique", "isdisjoint", "intersection", "intersect1d")
    if any(func_name(c) in checks for f in files for c in f.calls):
        return []
    for f in files:
        split = next((c for c in f.calls if f.is_split(c)), None)
        if split is not None:
            f.add(split.lineno, "duplicates-unchecked", f"`{short(split)}` 切分前后都没有检查重复样本")
            return [f.found[-1]]
    return []


def lint(sources: dict[str, str]) -> tuple[list[dict], dict[str, str]]:
    """Every finding over the files {relative path: text}, and the files that do not parse."""
    files, broken = [], {}
    for rel, text in sources.items():
        try:
            files.append(File(rel, text))
        except SyntaxError as e:
            broken[rel] = f"line {e.lineno}: {e.msg}"
    found = [x for f in files for x in f.run()]
    found += randomness(files) + time_series(files) + duplicates(files)
    return sorted(found, key=lambda x: (x["file"], x["line"], x["rule"])), broken


__all__ = ["RULES", "lint"]
