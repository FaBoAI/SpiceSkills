# SpiceSkills

SPICE のテスト補助ツールと検証ノウハウを共有するリポジトリです。[FaBoAI/KiCadMCP](https://github.com/FaBoAI/KiCadMCP) の `run_spice` / `read_raw` / `summarize_raw` を利用し、事前仕様、波形の判定、負例、コーナー生成、時間刻みの収束比較を補います。シミュレータや RAW parser は再実装しません。

## 導入と実行

Python 3.11 以上を使用します。両リポジトリを隣接して clone した例です。シミュレータは別途導入してください。KiCadMCP の Python API は追加依存なしで使用でき、MCP server の起動は不要です。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e ../KiCadMCP -e .
.venv/bin/spice-verify run examples/rc/lowpass.cir examples/rc/spec.json --engine ngspice --output-dir runs
.venv/bin/spice-verify corners examples/rc/lowpass.template.cir examples/rc/corners.json --output-dir runs/rc-corners
```

KiCadMCP API の動作確認対象: commit `9afc43b7237e6f86e7defb5188737aa2d862d48c`。依存パッケージのバージョン指定に加え、再現が必要な検証では実際の commit、エンジンのバージョン、モデルとデッキのハッシュを記録してください。任意の未信頼デッキを実行する sandbox は提供しません。

`run` は新しい実行フォルダへログ、RAW、実行 receipt と判定 JSON を保存します。`assess` は既存 receipt の再判定、`compare` は半刻みで再実行した指標の比較に使えます。いずれも既存の結果ファイルを上書きしません。

| 結果 | 意味 |
|---|---|
| `PASS` / `FAIL` | 完走した要件ケースの事前仕様判定 |
| `NEGATIVE_CONTROL_CONFIRMED` / `NEGATIVE_CONTROL_FAILED` | 負例が期待通りだったか。保護成功件数には加えない |
| `INCOMPLETE` | 未完走、必要な波形の欠落、比較の前提不足など |
| `STABLE` / `UNSTABLE` | 指定した半刻み比較の指標差。仕様適合やモデル妥当性とは別 |

## Skill とノウハウ

[spice-verification/SKILL.md](skills/spice-verification/SKILL.md) を Skill として利用できます。詳細は [JSON 仕様と結果契約](skills/spice-verification/references/acceptance.md)、[日本語の検証ノウハウ](skills/spice-verification/references/knowhow-ja.md) を参照してください。サンプルは理想 RC 回路だけを使い、メーカー非再配布モデルや案件固有データは含みません。

## テスト

```sh
.venv/bin/python -m unittest discover -s tests -v
```

単体テストは標準ライブラリと KiCadMCP のみで動きます。ngspice が PATH にある場合は、合成 RC の実行・波形判定と半刻み比較も検査します。未導入の場合、この結合テストだけを明示的に skip します。数値計算の完走・仕様適合・モデルの適用範囲を分けて報告し、実機検証の代用にしません。
