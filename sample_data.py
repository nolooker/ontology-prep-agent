"""
원천 CSV(단일 또는 여러 파일)에서 재현 가능한 무작위 샘플을 추출한다.
여러 파일(예: 시도별로 나뉜 파일)을 함께 넘기면 전체를 하나의 모집단으로
보고 저수지 표본추출(reservoir sampling)로 균등 추출한다.

사용법 (단일 파일):
    python sample_data.py --input data/raw/서울.csv --n 30 --output data/samples/sample_30.csv

사용법 (여러 파일, 전국 통합 무작위 추출):
    python sample_data.py --input data/raw/*.csv --n 50 --output data/samples/sample_50.csv

주의:
- 원천 CSV가 매우 크면(수백만 행) 청크 단위로 읽어 메모리를 절약한다.
- 여러 파일을 넘길 경우 --input에 여러 경로를 나열하거나 쉘 글롭을 사용한다.
"""
import argparse
import glob
import random

import pandas as pd


def simple_sample(input_path: str, n: int, seed: int, encoding: str) -> pd.DataFrame:
    df = pd.read_csv(input_path, encoding=encoding)
    n = min(n, len(df))
    return df.sample(n=n, random_state=seed)


def reservoir_sample(input_paths: list, n: int, seed: int, chunksize: int, encoding: str) -> pd.DataFrame:
    """여러 CSV 파일을 하나의 모집단으로 취급해 균등 무작위 샘플을 뽑는다."""
    rng = random.Random(seed)
    reservoir = []
    total_seen = 0
    header_cols = None

    for path in input_paths:
        for chunk in pd.read_csv(path, chunksize=chunksize, encoding=encoding):
            if header_cols is None:
                header_cols = list(chunk.columns)
            for _, row in chunk.iterrows():
                total_seen += 1
                if len(reservoir) < n:
                    reservoir.append(row)
                else:
                    j = rng.randint(0, total_seen - 1)
                    if j < n:
                        reservoir[j] = row

    print(f"전체 모집단 {total_seen}건 중 {len(reservoir)}건 추출")
    return pd.DataFrame(reservoir, columns=header_cols)


def main():
    parser = argparse.ArgumentParser(description="CSV 무작위 샘플 추출")
    parser.add_argument("--input", required=True, nargs="+",
                         help="원천 CSV 경로 (여러 개 나열 가능, 글롭 패턴 가능)")
    parser.add_argument("--output", required=True, help="샘플 저장 경로")
    parser.add_argument("--n", type=int, default=30, help="샘플 행 수")
    parser.add_argument("--seed", type=int, default=42, help="재현성을 위한 랜덤 시드")
    parser.add_argument("--chunksize", type=int, default=None,
                         help="지정 시 청크 단위 저수지 표본추출 사용 (대용량 파일용, 파일이 여러 개면 자동 적용)")
    parser.add_argument("--encoding", default="utf-8", help="원천 CSV 인코딩 (cp949 필요할 수 있음)")
    args = parser.parse_args()

    # 글롭 패턴 확장 (쉘이 확장 안 해줬을 경우 대비)
    input_paths = []
    for p in args.input:
        matched = glob.glob(p)
        input_paths.extend(matched if matched else [p])

    if len(input_paths) > 1 or args.chunksize:
        chunksize = args.chunksize or 50000
        sample_df = reservoir_sample(input_paths, args.n, args.seed, chunksize, args.encoding)
    else:
        sample_df = simple_sample(input_paths[0], args.n, args.seed, args.encoding)

    sample_df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"샘플 {len(sample_df)}건을 {args.output}에 저장했습니다.")
    print(f"컬럼: {list(sample_df.columns)}")


if __name__ == "__main__":
    main()
