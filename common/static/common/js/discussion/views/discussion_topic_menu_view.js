/* globals Backbone, _, DiscussionUtil */

(function() {
    'use strict';
    if (Backbone) {
        this.DiscussionTopicMenuView = Backbone.View.extend({
            events: {
                'change input[name="create-post-theme"]': 'handleTopicEvent'
            },

            attributes: {
                class: 'post-field'
            },

            initialize: function(options) {
                this.course_settings = options.course_settings;
                this.currentTopicId = options.topicId;
                this.group_name = options.group_name;
                _.bindAll(this,
                    'handleTopicEvent'
                );
                return this;
            },

            render: function() {
                var $general, context = _.clone(this.course_settings.attributes), themCount = {count: 0};

                context.topics_html = this.renderCategoryMap(this.course_settings.get('category_map'), null, themCount);
                DiscussionUtil.themeCount = themCount.count
                this.renderFilterColors();
                edx.HtmlUtils.setHtml(this.$el, edx.HtmlUtils.template($('#topic-template').html())(context));

                $general = this.$('label.radio-theme-input:contains(General)').find('input[name="create-post-theme"]');  // always return array.

                if (this.getCurrentTopicId()) {
                    this.setTopic(this.$('input[name="create-post-theme"]').filter(
                        '[data-discussion-id="' + this.getCurrentTopicId() + '"]'
                    ));
                } else if ($general.length > 0) {
                    this.setTopic($general.first());
                } else {
                    this.setTopic(this.$('input[name="create-post-theme"]').first());
                }
                return this.$el;
            },

            renderFilterColors: function(){
                $('li.forum-nav-browse-menu-item').each(function(index, item){
                    var $item = $(item);
                    var text = $item.find('span.subcategory-text').text().trim();
                    $item.find('span.theme-color').css(
                      'background-color', DiscussionUtil.assignTheme(index, text)
                    );
                });
            },

            renderCategoryMap: function(map, label, themeCount) {
                var categoryTemplate = edx.HtmlUtils.template($('#new-post-menu-category-template').html()),
                    entryTemplate = edx.HtmlUtils.template($('#new-post-menu-entry-template').html()),
                    mappedCategorySnippets = _.map(map.children, function(child) {
                        var entry,
                            html = '',
                            name = child[0], // child[0] is the category name
                            type = child[1]; // child[1] is the type (i.e. 'entry' or 'subcategory')
                        if (_.has(map.entries, name) && type === 'entry') {
                            entry = map.entries[name];
                            html = entryTemplate({
                                text: name,
                                label: label,
                                id: entry.id,
                                is_divided: entry.is_divided,
                                theme_color: DiscussionUtil.assignTheme(entry.color, name),
                            });
                            themeCount.count++;
                        }
                        else { // subcategory
                            html = categoryTemplate({
                                text: name,
                                entries: this.renderCategoryMap(map.subcategories[name], name, themeCount)
                            });
                        }
                        return html;
                    }, this);

                return edx.HtmlUtils.joinHtml.apply(null, mappedCategorySnippets);
            },

            handleTopicEvent: function(event) {
                var a = $(event.target);
                this.setTopic(a);
                return this;
            },

            setTopic: function($target) {
                if ($target.data('discussion-id')) {
                    this.topicText = this.getFullTopicName($target);
                    this.currentTopicId = $target.data('discussion-id');
                    $target.prop('checked', true);
                    this.trigger('thread:topic_change', $target);
                }
                return this;
            },

            getCurrentTopicId: function() {
                return this.currentTopicId;
            },

            /**
             * Return full name for the `topicElement` if it is passed.
             * Otherwise, full name for the current topic will be returned.
             * @param {jQuery Element} [topicElement]
             * @return {String}
             */
            getFullTopicName: function(topicElement) {
                var name;
                if (topicElement) {
                    name = topicElement.val();
                    // _.each(topicElement.parents('label.radio-theme-input'), function(item) {
                    //     name = $(item).attr('label') + ' / ' + name;
                    // });
                    if(name !== 'General'){
                        name = topicElement.attr('label') + ' / ' + name;
                    }
                    return name;
                } else {
                    return this.topicText;
                }
            }
        });
    }
}).call(this);
