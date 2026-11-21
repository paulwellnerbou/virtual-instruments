// The highlight is its own element under the buttons, so it can slide from one choice to the next.
// The choice shows at once on click; the server's answer only confirms it.
export default {
  template: `
    <div class="vi-segmented" role="group" :class="{ 'vi-segmented-ready': ready }">
      <div class="vi-segmented-thumb" :style="thumb"></div>
      <button v-for="option in options" :key="option.value" type="button" class="vi-segment"
              :aria-pressed="String(option.value === selected)" :disabled="disable" @click="choose(option.value)">
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
    return { selected: this.modelValue, thumb: { left: "0px", width: "0px" }, ready: false };
  },
  watch: {
    modelValue(value) {
      this.selected = value;
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
      const next = this.clearable && value === this.selected ? null : value;
      if (next === this.selected) return;
      this.selected = next;
      this.$emit("update:modelValue", next);
      this.$nextTick(this.measure);
    },
    measure() {
      const index = this.options.findIndex((option) => option.value === this.selected);
      const button = this.$el.querySelectorAll(".vi-segment")[index];
      this.thumb = button
        ? { left: button.offsetLeft + "px", width: button.offsetWidth + "px" }
        : { left: this.thumb.left, width: "0px" };
    },
  },
};
