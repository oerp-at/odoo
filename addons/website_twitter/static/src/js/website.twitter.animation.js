(function () {
    'use strict';
    var website = openerp.website,
        qweb = openerp.qweb;
 
    qweb.add_template('/website_twitter/static/src/xml/website.twitter.xml');
    if (!website.snippet) website.snippet = {};
    website.snippet.animationRegistry.twitter = website.snippet.Animation.extend({
        selector: ".twitter",
        start: function () {
            var self = this;
            var timeline = this.$target.find(".twitter_timeline");

            this.$target.on('click', '.twitter_timeline .tweet', function($event) {
                if ($event.target.tagName.toLowerCase() !== "a") {
                    var url = $($event.currentTarget).data('url');
                    if (url) {
                        window.open(url, '_blank');
                    }
                    else {
                        debugger;
                    }
                }
            });
            $("<center><div><img src='/website_twitter/static/src/img/loadtweet.gif'></div></center>").appendTo(timeline);
            openerp.jsonRpc('/get_favorites','call', {}).then(function(data) {
                self.$target.find(".twitter_timeline").empty();
                if (data.error) {
                    self.error(data);
                }
                else {
                    self.render(data);
                    self.setupMouseEvents();
                }
             });
        },
        stop: function() {
            $(this).find('.scrollWrapper').each(function(index, el){
                self.stop_scrolling($(el));
            });
            this.clearMouseEvents();
        },
        error: function(data){
            var $error = $(qweb.render("website.Twitter.Error", {'data': data}));
            $error.appendTo(this.$target.find(".twitter_timeline"));
        },
        parse_tweet: function (tweet) {
            var create_link = function (url, text) {
                var c = $("<a>", {
                    text: text,
                    href: url,
                    target: "_blank"
                });
                return c.prop("outerHTML");
            };
            return tweet.text.replace(/[A-Za-z]+:\/\/[A-Za-z0-9-_]+\.[A-Za-z0-9-_:%&~\?\/.=]+/g,
                                     function (url) { return create_link(url, url) })
                             .replace(/[@]+[A-Za-z0-9_]+/g,
                                     function (screen_name) { return create_link("http://twitter.com/" + screen_name.replace("@",""), screen_name); })
                             .replace(/[#]+[A-Za-z0-9_]+/g,
                                     function (hashtag) { return create_link("http://twitter.com/search?q="+hashtag.replace("#",""), hashtag); });
        },
        parse_date: function(tweet) {
            if (_.isEmpty(tweet.created_at)) return "";
            var v = tweet.created_at.split(' ');
            var d = new Date(Date.parse(v[1]+" "+v[2]+", "+v[5]+" "+v[3]+" UTC"));
            return d.toDateString();
        },
        setupMouseEvents: function() {
            var self = this;
            this.$scroller.mouseenter(function() {
                $(this).find('.scrollWrapper').each(function(index, el){
                    self.stop_scrolling($(el));
                });
            }).mouseleave(function() {
                 $(this).find('.scrollWrapper').each(function(index, el){
                    self.start_scrolling($(el));
                });
            });
        },
        clearMouseEvents: function() {
            if (this.$scroller) {
                this.$scroller.off('mouseenter')
                              .off('mouseleave');
            }
        },
        render: function(data){
            var self = this;
            var timeline =  this.$target.find(".twitter_timeline");
            var tweets = [];
            $.each(data, function (e, tweet) {
                tweet.created_at = self.parse_date(tweet);
                tweet.text = self.parse_tweet(tweet);
                tweets.push(qweb.render("website.Twitter.Tweet", {'tweet': tweet}));
            });
            
            var f = Math.floor(tweets.length / 3);
            var tweet_slice = [tweets.slice(0, f).join(" "), tweets.slice(f, f * 2).join(" "), tweets.slice(f * 2, tweets.length).join(" ")];
            
            this.$scroller = $(qweb.render("website.Twitter.Scroller"));
            this.$scroller.appendTo(this.$target.find(".twitter_timeline"));
            this.$scroller.find("div[id^='scroller']").each(function(index, element){
                var scrollWrapper = $('<div class="scrollWrapper"></div>');
                var scrollableArea = $('<div class="scrollableArea"></div>');
                scrollWrapper.append(scrollableArea)
                             .data('scrollableArea', scrollableArea);
                scrollableArea.append(tweet_slice[index]);
                $(element).append(scrollWrapper);
                scrollableArea.width(self.get_wrapper_width(scrollableArea));
                scrollWrapper.scrollLeft(index*180);
                self.start_scrolling(scrollWrapper);
            });
        },
        get_wrapper_width: function(wrapper){
            var total_width = 0;
            wrapper.children().each(function(){
                total_width += $(this).outerWidth(true);
            });
            return total_width;
        },
        start_scrolling: function(wrapper){
            var self = this;
            wrapper.data("getNextElementWidth", true);
            wrapper.data("autoScrollingInterval", setInterval(function () {
                wrapper.scrollLeft(wrapper.scrollLeft() + 1);
                self.swap_right(wrapper);
            }, 20));
        },
        stop_scrolling: function(wrapper){
            clearInterval(wrapper.data('autoScrollingInterval'));
        },
        swap_right: function(wrapper){
            if (wrapper.data("getNextElementWidth")) {
                wrapper.data("swapAt", wrapper.data("scrollableArea").children(":first").outerWidth(true));
                wrapper.data("getNextElementWidth", false);
            }
            if (wrapper.data("swapAt") <= wrapper.scrollLeft()){
                var swap_el = wrapper.data("scrollableArea").children(":first").detach();
                wrapper.data("scrollableArea").append(swap_el);
                wrapper.scrollLeft(wrapper.scrollLeft() - swap_el.outerWidth(true));
                wrapper.data("getNextElementWidth", true);
            }
        },
    });

})();
