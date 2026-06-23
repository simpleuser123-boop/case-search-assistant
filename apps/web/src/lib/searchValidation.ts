export type SearchTrigger = "button" | "keyboard";

export type SearchValidationResult =
  | {
      cleaned: string;
      isValid: true;
      message: "";
    }
  | {
      cleaned: string;
      isValid: false;
      message: string;
    };

const MIN_MEANINGFUL_LENGTH = 10;

export function normalizeSearchInput(value: string) {
  return value.replace(/\s+/g, " ").trim();
}

export function getMeaningfulLength(value: string) {
  return Array.from(value.replace(/[\p{P}\p{S}\s]/gu, "")).length;
}

export function validateSearchInput(value: string): SearchValidationResult {
  const cleaned = normalizeSearchInput(value);

  if (!cleaned) {
    return {
      cleaned,
      isValid: false,
      message: "请输入案情描述后再检索。",
    };
  }

  if (getMeaningfulLength(cleaned) === 0) {
    return {
      cleaned,
      isValid: false,
      message: "输入内容不能只有标点符号。",
    };
  }

  if (getMeaningfulLength(cleaned) < MIN_MEANINGFUL_LENGTH) {
    return {
      cleaned,
      isValid: false,
      message: "请至少输入 10 个可识别的文字或数字，描述事实经过或争议焦点。",
    };
  }

  return {
    cleaned,
    isValid: true,
    message: "",
  };
}
