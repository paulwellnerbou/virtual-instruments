// The highlight is its own element under the buttons, so it can slide from one choice to the next.
// It shows the server's value only: a choice the server refuses never lights up. The server is local, so a
// click still moves it at once.
export default {
  template: `
    <div class="vi-segmented" role="group" :class="{ 'vi-segmented-ready': ready }">
      <div class="vi-segmented-thumb" :style="thumb"></div>
      <button v-for="option in options" :key="option.value" type="button" class="vi-segment"
              :aria-pressed="String(option.value === modelValue)" :disabled="disable" @click="choose(option.value)">
        {{ option.label }}
      </button>
    </div>
  `,
  props: {
    options: Array,
    modelValue: { default: null },
    clearable: Boolean,
    disable: Boolean,
  },
  emits: ["update:modelValue"],
  data() {
    return { thumb: { left: "0px", width: "0px" }, ready: false };
  },
  watch: {
    modelValue() {
      this.$nextTick(this.measure);
    },
    options() {
      this.$nextTick(this.measure);
    },
  },
  mounted() {
    this.measure();
    // A control that appears or changes size (a step shown, a font loaded) moves its highlight without
    // sliding; only a new choice slides.
    this.resized = new ResizeObserver(() => {
      this.ready = false;
      this.measure();
      requestAnimationFrame(() => (this.ready = true));
    });
    this.resized.observe(this.$el);
  },
  beforeUnmount() {
    this.resized.disconnect();
  },
  methods: {
    choose(value) {
      if (this.disable) return;
      const next = this.clearable && value === this.modelValue ? null : value;
      if (next !== this.modelValue) this.$emit("update:modelValue", next);
    },
    measure() {
      const index = this.options.findIndex((option) => option.value === this.modelValue);
      const button = this.$el.querySelectorAll(".vi-segment")[index];
      this.thumb = button
        ? { left: button.offsetLeft + "px", width: button.offsetWidth + "px" }
        : { left: this.thumb.left, width: "0px" };
    },
  },
};
