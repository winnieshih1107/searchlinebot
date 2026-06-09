# =============================================================================
# 50 Startups — Multiple Linear Regression (CRISP-DM)
# =============================================================================

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import seaborn as sns
import statsmodels.api as sm
from statsmodels.stats.outliers_influence import variance_inflation_factor
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.linear_model import LinearRegression, LassoCV
from sklearn.preprocessing import StandardScaler
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.feature_selection import SelectKBest, f_regression, RFE
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
import warnings
import sys
import io
warnings.filterwarnings("ignore")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

plt.rcParams["figure.dpi"] = 110
plt.rcParams["font.size"] = 10

# =============================================================================
# PHASE 1 — BUSINESS UNDERSTANDING
# =============================================================================
print("=" * 65)
print("PHASE 1: BUSINESS UNDERSTANDING")
print("=" * 65)
print("""
Goal   : Predict startup profit from R&D, Admin, Marketing spend
         and State, to help investors allocate capital efficiently.

Target : Profit  (continuous → regression problem)
Success: R2 > 0.90 on held-out test set
""")

# =============================================================================
# PHASE 2 — DATA UNDERSTANDING
# =============================================================================
print("=" * 65)
print("PHASE 2: DATA UNDERSTANDING")
print("=" * 65)

df = pd.read_csv(r"D:\wi\50_Startups.csv")
numeric_cols = ["R&D Spend", "Administration", "Marketing Spend", "Profit"]

print(f"\nShape  : {df.shape[0]} rows x {df.shape[1]} columns")
print(f"Columns: {list(df.columns)}")
print("\n--- First 5 rows ---")
print(df.head())

print("\n--- Descriptive Statistics ---")
print(df.describe().round(2))

print("\n--- Missing Values ---")
print(df.isnull().sum())

print("\n--- State Distribution (Categorical — needs encoding) ---")
print(df["State"].value_counts())

# 2.1 Zero values
print("\n--- 2.1 Zero Values ---")
for c in numeric_cols:
    n = (df[c] == 0).sum()
    note = "  [valid business zeros — no imputation needed]" if n > 0 else ""
    print(f"  {c:<22}: {n} zeros{note}")

# 2.2 Skewness
print("\n--- 2.2 Skewness ---")
for c in numeric_cols:
    sk = df[c].skew()
    status = "[SKEWED > 0.5 — consider log transform]" if abs(sk) > 0.5 else "[OK — within acceptable range]"
    print(f"  {c:<22}: {sk:+.3f}  {status}")

# 2.3 Outliers (IQR x1.5)
print("\n--- 2.3 Outliers (IQR method) ---")
for c in numeric_cols:
    Q1, Q3 = df[c].quantile([0.25, 0.75])
    IQR = Q3 - Q1
    lo, hi = Q1 - 1.5 * IQR, Q3 + 1.5 * IQR
    outs = df[(df[c] < lo) | (df[c] > hi)][c]
    note = "  <-- review" if len(outs) > 0 else ""
    print(f"  {c:<22}: {len(outs)} outlier(s)  bounds=[{lo:,.0f}, {hi:,.0f}]{note}")

# 2.4 Feature scale — motivation for StandardScaler
print("\n--- 2.4 Feature Scale (std comparison) ---")
for c in numeric_cols[:-1]:
    print(f"  {c:<22}: min={df[c].min():>8,.0f}  max={df[c].max():>8,.0f}  std={df[c].std():>8,.0f}  [NEEDS SCALING]")

# 2.5 Multicollinearity (VIF)
print("\n--- 2.5 Multicollinearity (VIF) ---")
Xvif = df[["R&D Spend", "Administration", "Marketing Spend"]].copy()
Xvif["const"] = 1
for i, col in enumerate(["R&D Spend", "Administration", "Marketing Spend"]):
    vif = variance_inflation_factor(Xvif.values, i)
    status = "[HIGH — consider removing]" if vif > 5 else "[OK]"
    print(f"  {col:<22}: VIF = {vif:.2f}  {status}")

# 2.6 Correlation + OLS p-values — identify weak features
print("\n--- 2.6 Profit Correlation & OLS p-values ---")
df_ohe = pd.get_dummies(df, columns=["State"], drop_first=True)
X_all = df_ohe.drop("Profit", axis=1).astype(float)
y_all = df_ohe["Profit"]
ols_full = sm.OLS(y_all, sm.add_constant(X_all)).fit()
corr_with_profit = df[numeric_cols].corr()["Profit"].drop("Profit")
pvals = ols_full.pvalues.drop("const")

print(f"\n  {'Feature':<25} {'Corr w/ Profit':>15} {'p-value':>10}  Decision")
print("  " + "-" * 65)
for feat in X_all.columns:
    corr_v = corr_with_profit.get(feat, float("nan"))
    pv = pvals[feat]
    decision = "KEEP" if pv < 0.05 else "DROP (not significant)"
    print(f"  {feat:<25} {corr_v:>15.4f} {pv:>10.4f}  {decision}")

# ---- Visualisation Fig 1: Distributions + Correlation ----
fig, axes = plt.subplots(2, 3, figsize=(15, 9))
fig.suptitle("Phase 2 — Data Understanding (Distributions)", fontsize=14, fontweight="bold")

for i, col in enumerate(numeric_cols):
    ax = axes[i // 3][i % 3]
    ax.hist(df[col], bins=12, color="steelblue", edgecolor="white", alpha=0.85)
    ax.set_title(f"Distribution: {col}  (skew={df[col].skew():.2f})")
    ax.set_xlabel(col)
    ax.set_ylabel("Frequency")

axes[1][1].bar(df["State"].value_counts().index,
               df["State"].value_counts().values,
               color=["#4C72B0", "#DD8452", "#55A868"])
axes[1][1].set_title("State Distribution")
axes[1][1].set_ylabel("Count")

corr = df[numeric_cols].corr()
sns.heatmap(corr, annot=True, fmt=".2f", cmap="coolwarm",
            ax=axes[1][2], square=True, linewidths=0.5)
axes[1][2].set_title("Correlation Matrix")

plt.tight_layout()
plt.savefig(r"D:\wi\phase2a_distributions.png", bbox_inches="tight")
plt.show()
print("\n[Saved] phase2a_distributions.png")

# ---- Visualisation Fig 2: Scatter + Outliers + VIF ----
fig2, axes2 = plt.subplots(2, 3, figsize=(15, 9))
fig2.suptitle("Phase 2 — Feature Diagnosis (Scatter / Outliers / VIF)", fontsize=14, fontweight="bold")

feat_cols = ["R&D Spend", "Administration", "Marketing Spend"]
palette = {"R&D Spend": "#4C72B0", "Administration": "#C44E52", "Marketing Spend": "#55A868"}

# Scatter: feature vs Profit
for i, col in enumerate(feat_cols):
    ax = axes2[0][i]
    corr_v = df[[col, "Profit"]].corr().iloc[0, 1]
    ax.scatter(df[col], df["Profit"], color=palette[col], alpha=0.7, edgecolors="white", s=55)
    m, b = np.polyfit(df[col], df["Profit"], 1)
    x_line = np.linspace(df[col].min(), df[col].max(), 100)
    ax.plot(x_line, m * x_line + b, "r--", linewidth=1.5)
    ax.set_xlabel(col)
    ax.set_ylabel("Profit")
    ax.set_title(f"{col} vs Profit\ncorr = {corr_v:.3f}")

# Boxplots for outlier detection
for i, col in enumerate(feat_cols):
    ax = axes2[1][i]
    bp = ax.boxplot(df[col], vert=True, patch_artist=True,
                    boxprops=dict(facecolor=palette[col], alpha=0.6),
                    medianprops=dict(color="black", linewidth=2))
    Q1, Q3 = df[col].quantile([0.25, 0.75])
    IQR = Q3 - Q1
    n_out = len(df[(df[col] < Q1 - 1.5*IQR) | (df[col] > Q3 + 1.5*IQR)])
    ax.set_title(f"Boxplot: {col}\nOutliers: {n_out}")
    ax.set_ylabel(col)
    ax.set_xticks([])

plt.tight_layout()
plt.savefig(r"D:\wi\phase2b_diagnosis.png", bbox_inches="tight")
plt.show()
print("[Saved] phase2b_diagnosis.png")

print("\n--- Phase 2 Summary ---")
print("  State          : categorical  -> ONE-HOT ENCODING required")
print("  R&D Spend      : large scale  -> STANDARDSCALER required")
print("  Marketing Spend: large scale  -> STANDARDSCALER required")
print("  Administration : corr=0.20, p>0.05 -> DROP (not significant)")

# =============================================================================
# PHASE 3 — DATA PREPARATION
# =============================================================================
print("\n" + "=" * 65)
print("PHASE 3: DATA PREPARATION")
print("=" * 65)

# 3.1 One-Hot Encoding for State (drop_first avoids dummy variable trap)
df_ohe = pd.get_dummies(df, columns=["State"], drop_first=True)
print("\n[3.1] After One-Hot Encoding:")
print(df_ohe.head(3))

# ---------------------------------------------------------------------------
# 3.2 Feature Selection — Five methods
# ---------------------------------------------------------------------------
print("\n[3.2] Feature Selection — Five Methods")
print("  Candidates :", list(df_ohe.drop("Profit", axis=1).columns))

X_fs = df_ohe.drop("Profit", axis=1).astype(float)
y_fs = df_ohe["Profit"]
feat_names = list(X_fs.columns)
X_fs_scaled = StandardScaler().fit_transform(X_fs)   # needed for RFE & Lasso

# -- Method 1: Correlation Filter (|r| >= 0.30) --
corr_scores = X_fs.corrwith(y_fs).abs()
m1 = (corr_scores >= 0.30).astype(int)
print("\n  [Method 1] Correlation Filter  (threshold |r| >= 0.30)")
for f in feat_names:
    mark = "KEEP" if m1[f] else "drop"
    print(f"    {f:<25}: |r| = {corr_scores[f]:.4f}  -> {mark}")

# -- Method 2: SelectKBest with F-regression (top k=3) --
skb = SelectKBest(f_regression, k=3).fit(X_fs, y_fs)
m2 = pd.Series(skb.get_support().astype(int), index=feat_names)
f_scores = pd.Series(skb.scores_, index=feat_names)
print("\n  [Method 2] SelectKBest F-regression  (top k=3)")
for f in feat_names:
    mark = "KEEP" if m2[f] else "drop"
    print(f"    {f:<25}: F = {f_scores[f]:>9.3f}  -> {mark}")

# -- Method 3: Recursive Feature Elimination (top k=3, on scaled data) --
rfe = RFE(LinearRegression(), n_features_to_select=3).fit(X_fs_scaled, y_fs)
m3 = pd.Series(rfe.support_.astype(int), index=feat_names)
rfe_rank = pd.Series(rfe.ranking_, index=feat_names)
print("\n  [Method 3] RFE (LinearRegression, top k=3, scaled input)")
for f in feat_names:
    mark = "KEEP" if m3[f] else "drop"
    print(f"    {f:<25}: rank = {rfe_rank[f]}  -> {mark}")

# -- Method 4: Lasso (LassoCV on scaled data, coef != 0) --
lasso_cv = LassoCV(cv=5, random_state=42, max_iter=10000).fit(X_fs_scaled, y_fs)
m4 = pd.Series((np.abs(lasso_cv.coef_) > 1e-4).astype(int), index=feat_names)
lasso_coefs = pd.Series(lasso_cv.coef_, index=feat_names)
print(f"\n  [Method 4] Lasso (LassoCV, best alpha={lasso_cv.alpha_:.4f}, scaled input)")
for f in feat_names:
    mark = "KEEP" if m4[f] else "drop"
    print(f"    {f:<25}: coef = {lasso_coefs[f]:>10.4f}  -> {mark}")

# -- Method 5: Tree-based Importance (RandomForest, importance >= mean) --
rf = RandomForestRegressor(n_estimators=200, random_state=42).fit(X_fs, y_fs)
importances = pd.Series(rf.feature_importances_, index=feat_names)
m5 = (importances >= importances.mean()).astype(int)
print(f"\n  [Method 5] Random Forest Importance  (threshold = mean = {importances.mean():.4f})")
for f in feat_names:
    mark = "KEEP" if m5[f] else "drop"
    print(f"    {f:<25}: importance = {importances[f]:.4f}  -> {mark}")

# -- Consensus: majority vote (>= 3 / 5) --
sel_df = pd.DataFrame({
    "Correlation": m1,
    "SelectKBest":  m2,
    "RFE":          m3,
    "Lasso":        m4,
    "Tree":         m5,
}, index=feat_names)
sel_df["Votes"]    = sel_df.sum(axis=1)
sel_df["Decision"] = sel_df["Votes"].apply(lambda v: "KEEP" if v >= 3 else "DROP")

print("\n  --- Consensus Summary (>= 3/5 votes = KEEP) ---")
print(sel_df.to_string())

selected_features = sel_df[sel_df["Decision"] == "KEEP"].index.tolist()
dropped_features  = sel_df[sel_df["Decision"] == "DROP"].index.tolist()
print(f"\n  Selected : {selected_features}")
print(f"  Dropped  : {dropped_features}")

# ---- Visualisation: Feature Selection ----
fig3a, axes3a = plt.subplots(1, 2, figsize=(14, 5))
fig3a.suptitle("Phase 3.2 — Feature Selection: Five-Method Consensus", fontsize=13, fontweight="bold")

# Heatmap of selection matrix
sns.heatmap(sel_df[["Correlation","SelectKBest","RFE","Lasso","Tree"]],
            annot=True, fmt="d", cmap="RdYlGn", vmin=0, vmax=1,
            linewidths=0.5, ax=axes3a[0], cbar=False)
axes3a[0].set_title("Selection Matrix  (1=KEEP, 0=drop)")
axes3a[0].set_xticklabels(axes3a[0].get_xticklabels(), rotation=30, ha="right")

# Vote bar chart
vote_colors = ["#55A868" if d == "KEEP" else "#C44E52" for d in sel_df["Decision"]]
axes3a[1].barh(sel_df.index, sel_df["Votes"], color=vote_colors, edgecolor="white")
axes3a[1].axvline(3, color="black", linestyle="--", linewidth=1.5, label="Threshold (3/5)")
axes3a[1].set_xlabel("Votes")
axes3a[1].set_title("Feature Vote Count")
axes3a[1].set_xlim(0, 5.5)
for i, (votes, decision) in enumerate(zip(sel_df["Votes"], sel_df["Decision"])):
    axes3a[1].text(votes + 0.1, i, decision, va="center", fontsize=9)
axes3a[1].legend()

plt.tight_layout()
plt.savefig(r"D:\wi\phase3a_feature_selection.png", bbox_inches="tight")
plt.show()
print("\n[Saved] phase3a_feature_selection.png")

# ---------------------------------------------------------------------------
# 3.2 cont. — Performance Evaluation per Feature Selection Method
#             Train a LinearRegression for each method's feature subset,
#             report Train R², Test R², Test RMSE.  Uses same 80/20 split.
# ---------------------------------------------------------------------------
print("\n  --- Model Performance per Feature Selection Method ---")

X_eval_tr, X_eval_te, y_eval_tr, y_eval_te = train_test_split(
    X_fs, y_fs, test_size=0.2, random_state=42
)

method_labels = ["Correlation", "SelectKBest", "RFE", "Lasso", "Tree", "Consensus"]
method_masks  = [m1, m2, m3, m4, m5,
                 (sel_df["Decision"] == "KEEP").astype(int)]
method_colors = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#9467BD", "#8C564B"]

eval_rows = []
print(f"\n  {'Method':<13} {'Selected Features':<46} {'Train R2':>8} {'Test R2':>8} {'Test RMSE':>11}")
print("  " + "-" * 90)

for label, mask in zip(method_labels, method_masks):
    cols = [f for f, v in mask.items() if v == 1]
    if not cols:
        continue
    sc   = StandardScaler()
    Xtr  = sc.fit_transform(X_eval_tr[cols])
    Xte  = sc.transform(X_eval_te[cols])
    mdl  = LinearRegression().fit(Xtr, y_eval_tr)
    r2_tr  = r2_score(y_eval_tr, mdl.predict(Xtr))
    r2_te  = r2_score(y_eval_te, mdl.predict(Xte))
    rmse_te = np.sqrt(mean_squared_error(y_eval_te, mdl.predict(Xte)))
    feat_str = ", ".join(cols)
    print(f"  {label:<13} {feat_str:<46} {r2_tr:>8.4f} {r2_te:>8.4f} {rmse_te:>11,.0f}")
    eval_rows.append({"Method": label, "Test_R2": r2_te, "Train_R2": r2_tr,
                      "Test_RMSE": rmse_te})

eval_df = pd.DataFrame(eval_rows).set_index("Method")

# ---- Visualisation: R² and RMSE comparison ----
fig3c, axes3c = plt.subplots(1, 2, figsize=(14, 5))
fig3c.suptitle("Phase 3.2 — Feature Selection: R² & RMSE by Method",
               fontsize=13, fontweight="bold")

colors_bar = method_colors[:len(eval_df)]

# R² — grouped Train vs Test
x_pos  = np.arange(len(eval_df))
width  = 0.38
ax_r2  = axes3c[0]
bars1  = ax_r2.bar(x_pos - width/2, eval_df["Train_R2"], width,
                    label="Train R²", color="#4C72B0", alpha=0.85, edgecolor="white")
bars2  = ax_r2.bar(x_pos + width/2, eval_df["Test_R2"],  width,
                    label="Test R²",  color="#55A868", alpha=0.85, edgecolor="white")
ax_r2.axhline(0.90, color="red", linestyle="--", linewidth=1.4, label="Target R²=0.90")
ax_r2.set_xticks(x_pos)
ax_r2.set_xticklabels(eval_df.index, rotation=25, ha="right")
ax_r2.set_ylabel("R²")
ax_r2.set_ylim(min(eval_df["Test_R2"].min() - 0.05, 0.80), 1.02)
ax_r2.set_title("R² (Train vs Test)")
ax_r2.legend(fontsize=8)
for bar in bars2:
    ax_r2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.003,
               f"{bar.get_height():.3f}", ha="center", va="bottom", fontsize=7.5)

# RMSE — Test only
ax_rmse = axes3c[1]
rmse_bars = ax_rmse.bar(eval_df.index, eval_df["Test_RMSE"],
                         color=colors_bar, alpha=0.85, edgecolor="white")
ax_rmse.set_xticklabels(eval_df.index, rotation=25, ha="right")
ax_rmse.set_ylabel("RMSE ($)")
ax_rmse.set_title("Test RMSE by Feature Selection Method")
best_idx = eval_df["Test_RMSE"].idxmin()
for bar, method in zip(rmse_bars, eval_df.index):
    color = "red" if method == best_idx else "black"
    ax_rmse.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 100,
                 f"${bar.get_height():,.0f}", ha="center", va="bottom",
                 fontsize=7.5, color=color,
                 fontweight="bold" if method == best_idx else "normal")
ax_rmse.set_ylim(0, eval_df["Test_RMSE"].max() * 1.18)

plt.tight_layout()
plt.savefig(r"D:\wi\phase3c_performance.png", bbox_inches="tight")
plt.show()
print("[Saved] phase3c_performance.png")
print(f"\n  Best method by Test RMSE : {best_idx}")
print(f"  Best method by Test R²   : {eval_df['Test_R2'].idxmax()}")

# ---------------------------------------------------------------------------
# Separate selected features into numeric vs dummy for the pipeline
# ---------------------------------------------------------------------------
all_numeric = ["R&D Spend", "Administration", "Marketing Spend"]
all_dummy   = ["State_Florida", "State_New York"]
numeric_features = [f for f in selected_features if f in all_numeric]
dummy_features   = [f for f in selected_features if f in all_dummy]
print(f"\n  Numeric features (will be scaled) : {numeric_features}")
print(f"  Dummy features  (pass-through)    : {dummy_features}")

X = df_ohe[selected_features]
y = df_ohe["Profit"]

# 3.3 Train / Test split — 80/20
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)
print(f"\n[3.3] Train/Test Split:")
print(f"  Train set : {X_train.shape[0]} samples")
print(f"  Test  set : {X_test.shape[0]} samples")
print(f"  Features  : {list(X.columns)}")

# 3.4 StandardScaler — fit on train only via ColumnTransformer
#     Numeric features scaled; dummy features (0/1) passed through unchanged
preprocessor = ColumnTransformer(
    [("scaler", StandardScaler(), numeric_features)] +
    ([("passthrough", "passthrough", dummy_features)] if dummy_features else []),
    remainder="drop"
)

print("\n[3.4] StandardScaler (numeric only, dummies passed through):")
scaler_only = StandardScaler()
X_train_num_scaled = scaler_only.fit_transform(X_train[numeric_features])
before = X_train[numeric_features].describe().loc[["mean", "std"]].round(2)
after  = pd.DataFrame(X_train_num_scaled, columns=numeric_features).describe().loc[["mean","std"]].round(4)
print("\n  Before scaling:")
print(before.to_string())
print("\n  After scaling (mean~0, std~1):")
print(after.to_string())

# ---- Visualisation: Before vs After Scaling ----
fig3b, axes3b = plt.subplots(1, 2, figsize=(12, 5))
fig3b.suptitle("Phase 3.4 — StandardScaler: Before vs After", fontsize=13, fontweight="bold")

preprocessor_viz = ColumnTransformer(
    [("scaler", StandardScaler(), numeric_features)] +
    ([("passthrough", "passthrough", dummy_features)] if dummy_features else []),
    remainder="drop"
)
X_train_scaled_full = preprocessor_viz.fit_transform(X_train)
scaled_df = pd.DataFrame(X_train_scaled_full[:, :len(numeric_features)], columns=numeric_features)

axes3b[0].boxplot([X_train[c] for c in numeric_features],
                  labels=numeric_features, patch_artist=True,
                  boxprops=dict(facecolor="#4C72B0", alpha=0.6),
                  medianprops=dict(color="black", linewidth=2))
axes3b[0].set_title("Before Scaling (original units)")
axes3b[0].set_ylabel("Value")

axes3b[1].boxplot([scaled_df[c] for c in numeric_features],
                  labels=numeric_features, patch_artist=True,
                  boxprops=dict(facecolor="#55A868", alpha=0.6),
                  medianprops=dict(color="black", linewidth=2))
axes3b[1].set_title("After StandardScaler (mean=0, std=1)")
axes3b[1].set_ylabel("Standardised Value")
axes3b[1].axhline(0, color="red", linestyle="--", linewidth=1)

plt.tight_layout()
plt.savefig(r"D:\wi\phase3b_scaling.png", bbox_inches="tight")
plt.show()
print("[Saved] phase3b_scaling.png")

# 3.5 Build Pipeline: preprocessor + model (fitted in Phase 4)
pipeline = Pipeline([
    ("preprocessor", preprocessor),
    ("model",        LinearRegression()),
])
print("\n[3.5] Pipeline assembled:")
print("  ColumnTransformer(StandardScaler[numeric] + passthrough[dummies])")
print("  -> LinearRegression")

# =============================================================================
# PHASE 4 — MODELING
# =============================================================================
print("\n" + "=" * 65)
print("PHASE 4: MODELING")
print("=" * 65)

pipeline.fit(X_train, y_train)
model = pipeline.named_steps["model"]

print("\n--- Model Coefficients (on standardised features) ---")
feature_names = numeric_features + (dummy_features if dummy_features else [])
coef_df = pd.DataFrame({
    "Feature": feature_names,
    "Coefficient": model.coef_
}).sort_values("Coefficient", ascending=False)
print(coef_df.to_string(index=False))
print(f"\nIntercept: {model.intercept_:.2f}")

# =============================================================================
# PHASE 5 — EVALUATION
# =============================================================================
print("\n" + "=" * 65)
print("PHASE 5: EVALUATION")
print("=" * 65)

y_pred_train = pipeline.predict(X_train)
y_pred_test  = pipeline.predict(X_test)

def regression_report(y_true, y_pred, label):
    mae  = mean_absolute_error(y_true, y_pred)
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    r2   = r2_score(y_true, y_pred)
    mape = np.mean(np.abs((y_true - y_pred) / y_true)) * 100
    print(f"\n  [{label}]")
    print(f"  R²   : {r2:.4f}")
    print(f"  MAE  : ${mae:,.2f}")
    print(f"  RMSE : ${rmse:,.2f}")
    print(f"  MAPE : {mape:.2f}%")
    return r2, mae, rmse

r2_train, mae_train, rmse_train = regression_report(y_train, y_pred_train, "Train")
r2_test,  mae_test,  rmse_test  = regression_report(y_test,  y_pred_test,  "Test")

# Cross-Validation (5-fold on full dataset)
cv_scores = cross_val_score(pipeline, X, y, cv=5, scoring="r2")
print(f"\n  [5-Fold Cross-Validation R²]")
print(f"  Scores : {np.round(cv_scores, 4)}")
print(f"  Mean   : {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")

# ---- Visualisation: Evaluation ----
fig = plt.figure(figsize=(16, 10))
fig.suptitle("Phase 5 — Model Evaluation", fontsize=14, fontweight="bold")
gs = gridspec.GridSpec(2, 3, figure=fig)

# (1) Actual vs Predicted — Test
ax1 = fig.add_subplot(gs[0, 0])
ax1.scatter(y_test, y_pred_test, color="steelblue", edgecolors="white", s=70, alpha=0.85)
lims = [min(y_test.min(), y_pred_test.min()) * 0.95,
        max(y_test.max(), y_pred_test.max()) * 1.05]
ax1.plot(lims, lims, "r--", linewidth=1.5, label="Perfect fit")
ax1.set_xlabel("Actual Profit")
ax1.set_ylabel("Predicted Profit")
ax1.set_title(f"Actual vs Predicted (Test)\nR² = {r2_test:.4f}")
ax1.legend()

# (2) Residuals vs Predicted — Test
residuals = y_test - y_pred_test
ax2 = fig.add_subplot(gs[0, 1])
ax2.scatter(y_pred_test, residuals, color="coral", edgecolors="white", s=70, alpha=0.85)
ax2.axhline(0, color="black", linewidth=1.2, linestyle="--")
ax2.set_xlabel("Predicted Profit")
ax2.set_ylabel("Residual")
ax2.set_title("Residuals vs Predicted")

# (3) Residual distribution
ax3 = fig.add_subplot(gs[0, 2])
ax3.hist(residuals, bins=8, color="mediumseagreen", edgecolor="white", alpha=0.85)
ax3.axvline(0, color="red", linestyle="--", linewidth=1.2)
ax3.set_xlabel("Residual")
ax3.set_ylabel("Frequency")
ax3.set_title("Residual Distribution")

# (4) Feature Coefficients
ax4 = fig.add_subplot(gs[1, 0])
colors = ["#4C72B0" if c >= 0 else "#C44E52" for c in coef_df["Coefficient"]]
ax4.barh(coef_df["Feature"], coef_df["Coefficient"], color=colors)
ax4.axvline(0, color="black", linewidth=0.8)
ax4.set_xlabel("Coefficient Value")
ax4.set_title("Feature Coefficients")

# (5) Cross-Validation scores
ax5 = fig.add_subplot(gs[1, 1])
fold_labels = [f"Fold {i+1}" for i in range(len(cv_scores))]
bar_colors = ["#4C72B0" if s >= 0.9 else "#DD8452" for s in cv_scores]
ax5.bar(fold_labels, cv_scores, color=bar_colors, edgecolor="white")
ax5.axhline(cv_scores.mean(), color="red", linestyle="--",
            linewidth=1.5, label=f"Mean={cv_scores.mean():.3f}")
ax5.set_ylim(0, 1.05)
ax5.set_ylabel("R² Score")
ax5.set_title("5-Fold Cross-Validation R²")
ax5.legend()

# (6) Metrics comparison table
ax6 = fig.add_subplot(gs[1, 2])
ax6.axis("off")
table_data = [
    ["Metric", "Train", "Test"],
    ["R²",     f"{r2_train:.4f}",    f"{r2_test:.4f}"],
    ["MAE",    f"${mae_train:,.0f}", f"${mae_test:,.0f}"],
    ["RMSE",   f"${rmse_train:,.0f}",f"${rmse_test:,.0f}"],
]
tbl = ax6.table(cellText=table_data[1:], colLabels=table_data[0],
                loc="center", cellLoc="center")
tbl.auto_set_font_size(False)
tbl.set_fontsize(11)
tbl.scale(1.3, 2.0)
ax6.set_title("Performance Summary", pad=20)

plt.tight_layout()
plt.savefig(r"D:\wi\phase5_evaluation.png", bbox_inches="tight")
plt.show()
print("\n[Saved] phase5_evaluation.png")

# =============================================================================
# PHASE 6 — DEPLOYMENT (Prediction Demo)
# =============================================================================
print("\n" + "=" * 65)
print("PHASE 6: DEPLOYMENT — Prediction Demo")
print("=" * 65)

sample_companies = pd.DataFrame({
    "R&D Spend":       [150000, 50000, 0],
    "Administration":  [120000, 100000, 80000],
    "Marketing Spend": [300000, 100000, 50000],
})

predictions = pipeline.predict(sample_companies)

print("\n  Sample Predictions:")
print(f"  {'Company':<10} {'R&D':>10} {'Admin':>10} {'Mktg':>10} {'Predicted Profit':>18}")
print("  " + "-" * 62)
for i, pred in enumerate(predictions):
    row = sample_companies.iloc[i]
    print(f"  Company {i+1:<3} ${row['R&D Spend']:>9,.0f} "
          f"${row['Administration']:>9,.0f} "
          f"${row['Marketing Spend']:>9,.0f} ${pred:>16,.2f}")

print("\n" + "=" * 65)
print("CRISP-DM COMPLETE")
print("=" * 65)
print(f"""
Summary:
  - Best predictor : R&D Spend  (coef ≈ {coef_df[coef_df['Feature']=='R&D Spend']['Coefficient'].values[0]:.2f})
  - Model R2 (test): {r2_test:.4f}
  - CV R2  (5-fold): {cv_scores.mean():.4f} +/- {cv_scores.std():.4f}
  - Recommendation : Prioritise R&D investment for profit growth.
""")
