import io
import cv2
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as patches

class VisionEngine:
    @staticmethod
    def analyze(df_m5: pd.DataFrame, fvg_type: str = "NONE") -> dict:
        png_bytes, img = VisionEngine._generate_annotated_chart(df_m5, fvg_type)
        slope_data = VisionEngine._detect_trendline_slope(img)
        rejection_data = VisionEngine._detect_candle_rejection(df_m5)

        return {
            "image_bytes": png_bytes,
            "visual_bias": slope_data["visual_bias"],
            "avg_slope": slope_data["avg_slope"],
            "rejection": rejection_data["rejection"]
        }

    @staticmethod
    def _generate_annotated_chart(df: pd.DataFrame, fvg_type: str) -> tuple[bytes, np.ndarray]:
        fig, ax = plt.subplots(figsize=(9, 4.5), dpi=100)
        close_prices = df['close'].values
        ax.plot(close_prices, color='black', linewidth=1.5, label="Prix")

        if fvg_type == "BULLISH_FVG":
            b1_high = df.iloc[-4]['high']
            b3_low = df.iloc[-2]['low']
            rect = patches.Rectangle((len(df)-4, b1_high), 2, b3_low - b1_high, linewidth=1, edgecolor='g', facecolor='green', alpha=0.3)
            ax.add_patch(rect)
        elif fvg_type == "BEARISH_FVG":
            b3_high = df.iloc[-2]['high']
            b1_low = df.iloc[-4]['low']
            rect = patches.Rectangle((len(df)-4, b3_high), 2, b1_low - b3_high, linewidth=1, edgecolor='r', facecolor='red', alpha=0.3)
            ax.add_patch(rect)

        ax.grid(True, linestyle='--', alpha=0.5)
        ax.set_title("Analyse Visuelle OpenCV (M5)", fontsize=10)
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        png_bytes = buf.getvalue()

        file_bytes = np.asarray(bytearray(png_bytes), dtype=np.uint8)
        img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        plt.close(fig)

        return png_bytes, img

    @staticmethod
    def _detect_trendline_slope(img: np.ndarray) -> dict:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150, apertureSize=3)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=40, minLineLength=30, maxLineGap=10)

        slopes = []
        if lines is not None:
            for line in lines:
                coords = line[0] if len(line.shape) > 1 else line
                x1, y1, x2, y2 = coords
                if x2 - x1 != 0:
                    slopes.append((y2 - y1) / (x2 - x1))

        avg_slope = float(np.mean(slopes)) if slopes else 0.0
        visual_bias = "BULLISH_TRENDLINE" if avg_slope < -0.12 else ("BEARISH_TRENDLINE" if avg_slope > 0.12 else "NEUTRAL")

        return {"avg_slope": round(avg_slope, 4), "visual_bias": visual_bias}

    @staticmethod
    def _detect_candle_rejection(df: pd.DataFrame) -> dict:
        last = df.iloc[-2]
        open_p, close_p, high_p, low_p = last['open'], last['close'], last['high'], last['low']
        total_range = high_p - low_p

        if total_range == 0:
            return {"rejection": "NONE"}

        body_size = abs(close_p - open_p)
        upper_wick = high_p - max(open_p, close_p)
        lower_wick = min(open_p, close_p) - low_p

        if lower_wick / total_range > 0.50 and body_size / total_range < 0.35:
            return {"rejection": "BULLISH_REJECTION"}
        elif upper_wick / total_range > 0.50 and body_size / total_range < 0.35:
            return {"rejection": "BEARISH_REJECTION"}

        return {"rejection": "NONE"}
